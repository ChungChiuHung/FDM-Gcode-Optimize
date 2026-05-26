import os
import re
import math
import shutil
import argparse
import itertools

# Module-level tuple for O(1) outer-wall tag lookup inside the hot move-write loop.
_OUTER_WALL_TAGS = ('outer wall', 'wall-outer', 'external')
# [!] CRITICAL FIX: 嚴格白名單系統隔離
# 只允許這些「真實模型特徵」觸發 AI 優化，確保機台校正線與硬體動作 100% 被保護
_MODEL_TAGS = ('wall', 'infill', 'surface', 'bridge', 'gap', 'skirt', 'brim', 'support')

# --- 導入物理插件 ---
from src.core.io_manager import AtomicGCodeWriter
from src.core.parser import GCodeParser
from src.physics.materials import detect_filament_type, get_material_profile
from src.physics.thermodynamics import get_base_temperature, calculate_thermal_state
from src.physics.kinematics import AntiResonanceBrake
from src.safety.bounds_enforcer import get_hardware_bounds
from src.safety.z_hop_injector import ZHopInjector  # 新增 Z-Hop 安全避障引擎

# --- 核心安全檢查：碰撞沙盒 ---
try:
    from src.safety.collision_sandbox import GCodeCollisionChecker
    HAS_AUDITOR = True
except ImportError:
    HAS_AUDITOR = False
    print("[!] 警告：找不到 src/safety/collision_sandbox.py，物理稽核已停用。")


def auto_optimize_gcode(input_path: str, is_heavy_toolhead: bool = True, enable_recommendations: bool = True, enable_experimental_sorting: bool = False):
    """
    高品質優化管線 (Non-Destructive + Thermal): 保留原始路徑，注入動力學與噴嘴熱力學補償。
    """
    if not os.path.exists(input_path):
        print(f"Error: File not found: {input_path}")
        return False

    parser = GCodeParser()

    # 1. 預讀配置與環境初始化
    header_lines = []
    with open(input_path, 'r', encoding='utf-8', errors='ignore') as f:
        for i, line in enumerate(f):
            header_lines.append(line)
            if "; CONFIG_BLOCK_END" in line or i > 4000:
                break
    header_text = "".join(header_lines)

    filament_type = detect_filament_type(header_text, input_path)
    mat_profile = get_material_profile(filament_type, header_text=header_text)
    base_temp = get_base_temperature(header_text)
    hw_min_x, hw_max_x, hw_min_y, hw_max_y, hw_max_z = get_hardware_bounds(header_text)

    # 提取機台型號 (用於除錯與日誌識別)
    machine_match = re.search(r';\s*printer_model\s*[:=]\s*([^\n\r]+)', header_text, re.IGNORECASE)
    machine_type = machine_match.group(1).strip() if machine_match else "Unknown Printer"

    # PathOptimizer.hybrid_sort is DEPRECATED per specs/architecture.md:
    # custom TSP/serpentine sorting destroys slicer collision-avoidance and
    # micro-kinematics (scarf seams, wipe-while-retract). Disabled by default;
    # only enabled when --enable-experimental-sorting is explicitly passed.
    if enable_experimental_sorting:
        print("[!] WARNING: Experimental path sorting is ENABLED. "
              "This may corrupt slicer geometry. See specs/architecture.md.")

    print("-" * 50)
    print(f"[*] 機型識別 (Machine) : {machine_type}")
    print(f"[*] 材料識別 (Material): {filament_type}")
    print(f"[*] 開始處理: 物理軌跡注入與熱平衡模式啟動...")
    print("-" * 50)

    # [!] 啟動 Z-Hop 幾何安全避障引擎 (依據材料自動給定抬升高度)
    hop_injector = ZHopInjector(z_hop=mat_profile.get("z_hop", 0.4))

    if enable_recommendations:
        print("\n" + "="*50)
        print("[*] AI QUALITY RECOMMENDATIONS ACTIVE:")
        print("  1. Small Circles: Set 'Minimum travel for retraction' to ~0.4mm to prevent stringing.")
        print("  2. First Layer (PEI): Use 0.5mm Initial Line Width & turn off cooling fan for layers 1-3.")
        print("  3. Check 'Wipe while retracting' is enabled for micro-features.")
        print("="*50 + "\n")

    # 2. 安全備份與輸出準備
    backup_path = input_path + ".bak"
    shutil.copy2(input_path, backup_path)
    output_path = input_path
    tmp_path = output_path + ".tmp"

    active_feat = None
    current_eq_state = "NORMAL"
    current_flow_override = 100
    prev_move = None
    FILAMENT_AREA = 2.405  # 1.75mm 線材截面積

    # 3. 核心迴圈：讀取 Generator 並寫入暫存檔
    with open(tmp_path, 'w', encoding='utf-8', newline='\n') as out:
        
        if enable_recommendations:
            out.write("; === H2C AI QUALITY RECOMMENDATIONS ===\n")
            out.write("; [!] Retraction Min Travel: Recommended 0.4mm for small hole stringing prevention.\n")
            out.write("; [!] PEI Plate Adhesion: Recommended 0.5mm initial line width.\n")
            out.write("; [!] GLASS BOTTOM: Automatically injecting +8% squish via M221 for Layer 1.\n")
            out.write("; ======================================\n\n")

        for layer in parser.parse_streaming(backup_path):
            if not layer: continue
            
            is_first_layer = any(m['end'][2] <= 0.4 for m in layer if m['type'] == 'extrude')

            # --- [!] CRITICAL FIX: 每層重置避障引擎，更新當前高度 ---
            current_layer_z = max((m['end'][2] for m in layer if m['type'] == 'extrude'), default=None)
            if current_layer_z is not None:
                hop_injector.reset_layer(current_layer_z)

            # Chunking moves into contiguous extrusion/retraction blocks and isolated travels.
            chunks = []
            curr_chunk = []
            for m in layer:
                if m['type'] == 'travel':
                    if curr_chunk:
                        chunks.append(curr_chunk)
                        curr_chunk = []
                    chunks.append([m])
                else:
                    curr_chunk.append(m)
            if curr_chunk:
                chunks.append(curr_chunk)

            for chunk in chunks:
                
                # --- A. 體積流量與噴嘴熱平衡計算 (Nozzle Thermodynamics) ---
                if base_temp and any(m['type'] == 'extrude' for m in chunk):
                    sample_feat = next((m.get('feature', '').lower() for m in chunk if m['type'] == 'extrude'), "")
                    is_system_chunk = not any(kw in sample_feat for kw in _MODEL_TAGS)
                    
                    if not is_system_chunk:
                        island_dist = sum(math.hypot(m['end'][0]-m['start'][0], m['end'][1]-m['start'][1]) for m in chunk if m['type'] == 'extrude')
                        island_e = sum(m['e_val'] for m in chunk if m['type'] == 'extrude')
                        
                        if island_dist > 0.001:
                            ref_speed = next((m['feedrate'] for m in chunk if m['type'] == 'extrude'), 3000) / 60.0
                            vol_flow = (island_e * FILAMENT_AREA) / (island_dist / ref_speed) if ref_speed > 0 else 0
                            
                            target_state, target_temp = calculate_thermal_state(base_temp, island_dist, vol_flow, is_first_layer)
                            if current_eq_state != target_state:
                                out.write(f"\n; --- AI Thermal Equalizer: {target_state} MODE ---\n")
                                out.write(f"M104 S{target_temp} ; 動態控溫控制壓力\n")
                                current_eq_state = target_state

                # --- B. 遍歷寫入 Chunk 內的指令 ---
                for move in chunk:
                    feat = move.get('feature')
                    feat_lower = (feat or "").lower()

                    # 使用白名單機制：非模型特徵（如校正線、換線動作）全視為系統保留區
                    is_system_move = not any(kw in feat_lower for kw in _MODEL_TAGS)

                    for meta_line in move.get('metadata', []):
                        if is_first_layer and move['type'] == 'travel' and not is_system_move:
                            if re.match(r'^M204\s+S', meta_line, re.IGNORECASE):
                                out.write("M204 S500 ; AI First-Layer Travel Clamp\n")
                                continue
                        out.write(meta_line + '\n')

                    # --- [!] CRITICAL FIX: 記錄牆面與頂面給避障引擎 ---
                    if move['type'] == 'extrude' and not is_system_move:
                        if any(kw in feat_lower for kw in ['outer wall', 'top surface', 'wall-outer', 'external']):
                            hop_injector.add_wall_seg(move['start'][0], move['start'][1], move['end'][0], move['end'][1])

                    pending_hop = ""
                    if move['type'] == 'travel' and not is_system_move:
                        pending_hop = hop_injector.get_hop_gcode(
                            frm=move['start'],
                            to=move['end'],
                            cur_z=move['start'][2],
                            travel_f=int(move['feedrate'])
                        )

                    if feat and feat != active_feat:
                        out.write(f"{feat}\n")
                        active_feat = feat
                        
                        # 安全的流量覆蓋：只針對真實模型特徵 (+8% Squish)，忽略系統校正
                        target_flow = 108 if (is_first_layer and not is_system_move) else 100
                        if current_flow_override != target_flow:
                            out.write(f"M221 S{target_flow} ; AI Glass-Bottom Flow Control\n")
                            current_flow_override = target_flow
                            
                    # 安全的切線擦拭
                    if move['type'] == 'travel' and prev_move and prev_move['type'] == 'extrude' and not is_system_move:
                        t_dist = math.hypot(move['end'][0] - move['start'][0], move['end'][1] - move['start'][1])
                        is_micro_feature = any(kw in feat_lower for kw in ['small', 'hole', 'circle'])
                        
                        if is_micro_feature or t_dist > 10.0:
                            px1, py1 = prev_move['start'][0], prev_move['start'][1]
                            px2, py2 = prev_move['end'][0], prev_move['end'][1]
                            p_dist = math.hypot(px2 - px1, py2 - py1)
                            if p_dist > 0.001:
                                wipe_dist = 1.5 if is_micro_feature else 2.0  
                                dx, dy = (px2 - px1) / p_dist, (py2 - py1) / p_dist
                                wx = max(hw_min_x, min(hw_max_x, px2 + dx * wipe_dist))
                                wy = max(hw_min_y, min(hw_max_y, py2 + dy * wipe_dist))
                                wipe_speed = prev_move['feedrate'] * 1.5
                                reason = "Micro-Feature" if is_micro_feature else "Long Travel"
                                out.write(f"; --- AI Tangential Wipe (Anti-Ooze | {reason}) ---\n")
                                out.write(f"G1 X{wx:.3f} Y{wy:.3f} F{int(wipe_speed)}\n")

                    # 4. Feedrate: Hull Line fix & First Layer Speed Clamp (安全隔離)
                    f_val = move['feedrate']
                    
                    if not is_system_move:
                        if is_first_layer and move['type'] == 'extrude':
                            # Glass Bottom Fix: 限制首層列印速度最高為 30mm/s (F1800)
                            f_val = min(f_val, 1800.0)
                        elif (active_feat and mat_profile.get("needs_uniform_speed")
                                and any(kw in active_feat.lower() for kw in _OUTER_WALL_TAGS)):
                            f_val = 3600
                        elif move['type'] != 'travel':
                            f_val *= mat_profile.get('speed_multiplier', 1.0)

                    # 5 & 6. Reconstruction / System Isolation
                    if is_system_move and 'raw_line' in move:
                        # 100% 完美物理隔離：一字不差地寫入原切片軟體的系統/校正指令！
                        out.write(f"{move['raw_line']}\n")
                    else:
                        # --- AI Z-Hop: use pre-computed pending_hop (already accounts for
                        #     brake coordination; feedrate is updated to clamped f_val) ---
                        is_hopped = False
                        if pending_hop:
                            # Re-issue with final clamped feedrate so the travel speed
                            # respects the first-layer cap applied in f_val above.
                            final_hop = hop_injector.get_hop_gcode(
                                frm=move['start'],
                                to=move['end'],
                                cur_z=move['start'][2],
                                travel_f=int(f_val)
                            )
                            if final_hop:
                                out.write(final_hop)
                                is_hopped = True

                        if not is_hopped:
                            # 模型特徵：套用坐標箝制與浮點數優化重構
                            ex = max(hw_min_x, min(hw_max_x, move['end'][0]))
                            ey = max(hw_min_y, min(hw_max_y, move['end'][1]))
                            ez = max(0.0, min(hw_max_z, move['end'][2]))
                            
                            xy_str = f" X{ex:.3f} Y{ey:.3f}" if move.get('has_xy', True) else ""
                            z_str = f" Z{ez:.3f}" if abs(ez - move['start'][2]) > 0.001 else ""
                            
                            raw_e = move.get('raw_e')
                            e_str = f" E{raw_e:.5f}" if raw_e is not None else ""
                            
                            g_cmd = move.get('g_code', 1)
                            arc_str = ""
                            if g_cmd in (2, 3):
                                if move.get('i') is not None: arc_str += f" I{move['i']:.3f}"
                                if move.get('j') is not None: arc_str += f" J{move['j']:.3f}"
                                if move.get('r') is not None: arc_str += f" R{move['r']:.3f}"
                                if move.get('p') is not None: arc_str += f" P{int(move['p'])}" # [!] 移除尾部 \n
                                
                            if xy_str or z_str or arc_str or e_str:
                                out.write(f"G{g_cmd}{xy_str}{z_str}{arc_str}{e_str} F{int(f_val)}\n")

                            # Inject pure physics dwell AFTER the toolhead arrives
                            if move['type'] == 'travel' and is_heavy_toolhead and not is_system_move:
                                dist = math.hypot(move['end'][0] - move['start'][0], move['end'][1] - move['start'][1])
                                brake_gcode = AntiResonanceBrake.inject_soft_stop(
                                    speed_mm_min=f_val,
                                    distance_mm=dist
                                )
                                if brake_gcode:
                                    out.write(brake_gcode + "\n")

                    prev_move = move

    # 4. Concatenate: header + body + footer (atomic — safe against crash/interrupt)
    try:
        with AtomicGCodeWriter.atomic_write(output_path) as out_f:
            out_f.write("".join(parser.header_lines))

            with open(tmp_path, 'r', encoding='utf-8') as body_f:
                shutil.copyfileobj(body_f, out_f)

            out_f.write("".join(parser.footer_lines))
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

    print(f"[*] 優化完畢。")

    # 5. 最終防線：碰撞沙盒稽核
    if HAS_AUDITOR:
        print("\n[Audit] 啟動閉環物理稽核，驗證路徑安全性...")
        checker = GCodeCollisionChecker()
        checker.run_check(output_path)

    return True


if __name__ == "__main__":
    parser_cli = argparse.ArgumentParser(description="H2C Geometric AI Pipeline v4.9")
    parser_cli.add_argument("input", help="Raw G-code path")
    parser_cli.add_argument("--disable-recommendations", action="store_true", help="Turn off AI slicing advice")
    parser_cli.add_argument("--enable-experimental-sorting", action="store_true",
                            help="[DEPRECATED] Enable PathOptimizer.hybrid_sort. "
                                 "Disabled by default — see specs/architecture.md.")
    args = parser_cli.parse_args()

    auto_optimize_gcode(
        args.input,
        enable_recommendations=not args.disable_recommendations,
        enable_experimental_sorting=args.enable_experimental_sorting,
    )