import math
from typing import List, Dict, Tuple

class PathOptimizer:
    """
    Hybrid Cluster-Sweep Algorithm with Bidirectional Momentum Alignment.
    """
    @staticmethod
    def calculate_distance(pt1: Tuple[float, float, float], pt2: Tuple[float, float, float]) -> float:
        return math.hypot(pt2[0] - pt1[0], pt2[1] - pt1[1])

    @staticmethod
    def reverse_island(island: List[Dict]) -> List[Dict]:
        """Safely reverses the printing direction of an extrusion path."""
        front_e_moves = []
        back_e_moves = []
        print_moves = []
        
        for move in island:
            # WIPE/RETRACTION FIX: Bookend pure E moves so they don't get reversed
            if not move.get('has_xy', True):
                if not print_moves: front_e_moves.append(move)
                else: back_e_moves.append(move)
            else:
                print_moves.append(move)
                
        if not print_moves: return island
        
        reversed_print_moves = []
        for move in reversed(print_moves):
            new_move = move.copy()
            # Swap start and end coordinates
            new_move['start'] = move['end']
            new_move['end'] = move['start']
            
            if 'metadata' in new_move:
                del new_move['metadata']
                
            # Safety: Abort reversal if it contains complex G2/G3 arcs 
            if new_move.get('g_code') in (2, 3):
                return island 
                
            reversed_print_moves.append(new_move)
            
        # Reassemble the island
        new_island = front_e_moves + reversed_print_moves + back_e_moves
        
        # Re-attach slicer metadata (Fan/Accel) to the NEW first move
        if island[0].get('metadata'):
            new_island[0]['metadata'] = island[0]['metadata']
            
        return new_island

    @staticmethod
    def cluster_islands(islands: List[List[Dict]], threshold: float = 15.0) -> List[List[List[Dict]]]:
        clusters = []
        unassigned = list(islands)
        while unassigned:
            current_cluster = [unassigned.pop(0)]
            clusters.append(current_cluster)
            while True:
                added = False
                for i in range(len(unassigned) - 1, -1, -1):
                    candidate = unassigned[i]
                    for island in current_cluster:
                        dist = PathOptimizer.calculate_distance(candidate[0]['start'], island[0]['start'])
                        if dist < threshold:
                            current_cluster.append(unassigned.pop(i))
                            added = True
                            break
                if not added: break
        return clusters

    @staticmethod
    def serpentine_sort(islands: List[List[Dict]], lane_width: float = 3.0) -> List[List[Dict]]:
        if not islands: return []
        lanes = {}
        for island in islands:
            lane_id = int(island[0]['start'][1] / lane_width)
            if lane_id not in lanes: lanes[lane_id] = []
            lanes[lane_id].append(island)
        
        optimized = []
        sweep_ltr = True
        for lid in sorted(lanes.keys()):
            lane_islands = sorted(lanes[lid], key=lambda i: i[0]['start'][0], reverse=not sweep_ltr)
            for item in lane_islands:
                feat = item[0].get('feature', '').lower()
                can_reverse = not ('wall' in feat or 'bridge' in feat)
                
                if can_reverse:
                    start_x = item[0]['start'][0]
                    end_x = item[-1]['end'][0]
                    if sweep_ltr and start_x > end_x: item = PathOptimizer.reverse_island(item)
                    elif not sweep_ltr and start_x < end_x: item = PathOptimizer.reverse_island(item)
                        
                optimized.append(item)
            sweep_ltr = not sweep_ltr
        return optimized

    @staticmethod
    def hybrid_sort(layer_moves: List[Dict], lane_width: float = 3.0, cluster_threshold: float = 15.0) -> List[List[Dict]]:
        islands, current = [], []
        accumulated_meta = []
        for m in layer_moves:
            accumulated_meta.extend(m.get('metadata', []))
            if m['type'] == 'extrude':
                m['metadata'] = accumulated_meta
                accumulated_meta = []
                current.append(m)
            elif m['type'] == 'travel' and current:
                islands.append(current)
                current = []
        if current: islands.append(current)
        if not islands: return []

        feature_blocks = []
        current_block = []
        current_feat = None
        for island in islands:
            feat = island[0].get('feature', 'Unknown')
            if feat != current_feat:
                if current_block: feature_blocks.append(current_block)
                current_block = []
                current_feat = feat
            current_block.append(island)
        if current_block: feature_blocks.append(current_block)

        optimized_sequence = []
        last_pos = (0.0, 0.0, 0.0)

        for block in feature_blocks:
            clusters = PathOptimizer.cluster_islands(block, cluster_threshold)
            sorted_clusters = []
            unvisited_clusters = clusters.copy()

            while unvisited_clusters:
                best_idx = 0
                min_dist = float('inf')
                for idx, cluster in enumerate(unvisited_clusters):
                    dist = PathOptimizer.calculate_distance(last_pos, cluster[0][0]['start'])
                    if dist < min_dist:
                        min_dist = dist
                        best_idx = idx

                closest_cluster = unvisited_clusters.pop(best_idx)
                sorted_clusters.append(closest_cluster)
                last_pos = closest_cluster[-1][-1]['end']

            for cluster in sorted_clusters:
                sorted_islands = PathOptimizer.serpentine_sort(cluster, lane_width)
                optimized_sequence.extend(sorted_islands)
                last_pos = sorted_islands[-1][-1]['end']

        return optimized_sequence