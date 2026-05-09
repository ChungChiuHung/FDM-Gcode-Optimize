from typing import Optional

def calculate_inertia_dampening(feature_type: Optional[str], is_first_layer: bool, is_travel: bool) -> int:
    """
    動態調整加速度 (M204) 以防止重型工具頭產生機台共振與水波紋。
    您可以根據自製工具頭的實際重量在這裡微調數值！
    
    參數:
        feature_type: 當前列印特徵 (如 'Outer wall', 'Gap fill')，可能為 None
        is_first_layer: 是否為第一層
        is_travel: 是否為純粹的空跑移動 (沒有擠出行為)
        
    回傳:
        int: 目標加速度數值 (mm/s^2)
    """
    # 1. 【首層鎖定】首層需要極度平滑，鎖定低加速度以防因慣性拉扯而脫膠
    if is_first_layer:
        return 1000 
        
    # 2. 【空跑移動】空跑時噴嘴沒有擠出阻力，無論什麼特徵都可允許全速移動節省時間
    if is_travel:
        return 5000

    # 安全防護：如果切片軟體沒有標記該行特徵，回傳預設安全值
    if not feature_type:
        return 3000
        
    feat_lower = feature_type.lower()
    
    # 3. 【緊湊幾何形狀】重踩煞車：微小縫隙與內部填充頻繁轉向，降低加速度以消除重型工具頭的物理共振
    if 'gap' in feat_lower or 'small' in feat_lower or 'internal' in feat_lower:
        return 1500 
        
    # 4. 【平滑長直線】全速加速：外牆通常為長直線或大圓弧，可容許較高加速度
    elif 'outer' in feat_lower:
        return 5000 
        
    # 5. 【預設安全值】其他一般特徵 (如普通填充、內牆)
    return 3000