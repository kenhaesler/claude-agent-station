/**
 * Simple layered DAG layout (simplified Sugiyama).
 * Assigns (x, y) positions to nodes based on dependency layers.
 */

export interface DagNode {
  id: string;
  label: string;
  status: string;
  dependsOn: string[];
}

export interface LayoutNode {
  id: string;
  label: string;
  status: string;
  x: number;
  y: number;
  layer: number;
}

export interface LayoutEdge {
  from: string;
  to: string;
  fromX: number;
  fromY: number;
  toX: number;
  toY: number;
}

export interface DagLayout {
  nodes: LayoutNode[];
  edges: LayoutEdge[];
  width: number;
  height: number;
}

const NODE_WIDTH = 160;
const NODE_HEIGHT = 48;
const LAYER_GAP = 80;
const NODE_GAP = 24;

/**
 * Compute layered layout for a DAG.
 * Layer 0 = nodes with no dependencies, layer N = max dependency depth.
 */
export function computeDagLayout(nodes: DagNode[]): DagLayout {
  if (nodes.length === 0) return { nodes: [], edges: [], width: 0, height: 0 };

  const nodeMap = new Map(nodes.map(n => [n.id, n]));

  // Assign layers via BFS (longest path from root)
  const layers = new Map<string, number>();

  function getLayer(id: string, visited = new Set<string>()): number {
    if (layers.has(id)) return layers.get(id)!;
    if (visited.has(id)) return 0; // cycle protection
    visited.add(id);

    const node = nodeMap.get(id);
    if (!node || node.dependsOn.length === 0) {
      layers.set(id, 0);
      return 0;
    }

    const maxDep = Math.max(...node.dependsOn
      .filter(d => nodeMap.has(d))
      .map(d => getLayer(d, visited)));
    const layer = maxDep + 1;
    layers.set(id, layer);
    return layer;
  }

  nodes.forEach(n => getLayer(n.id));

  // Group by layer
  const maxLayer = Math.max(...layers.values(), 0);
  const layerGroups: string[][] = Array.from({ length: maxLayer + 1 }, () => []);
  for (const [id, layer] of layers) {
    layerGroups[layer].push(id);
  }

  // Assign positions
  const layoutNodes: LayoutNode[] = [];
  const nodePositions = new Map<string, { x: number; y: number }>();

  for (let layer = 0; layer <= maxLayer; layer++) {
    const group = layerGroups[layer];
    const groupWidth = group.length * (NODE_WIDTH + NODE_GAP) - NODE_GAP;
    const startX = -groupWidth / 2 + NODE_WIDTH / 2;

    group.forEach((id, idx) => {
      const x = startX + idx * (NODE_WIDTH + NODE_GAP);
      const y = layer * (NODE_HEIGHT + LAYER_GAP);
      nodePositions.set(id, { x, y });
      const node = nodeMap.get(id)!;
      layoutNodes.push({ id, label: node.label, status: node.status, x, y, layer });
    });
  }

  // Normalize to positive coordinates
  const minX = Math.min(...layoutNodes.map(n => n.x));
  const minY = Math.min(...layoutNodes.map(n => n.y));
  const offsetX = -minX + 20;
  const offsetY = -minY + 20;

  layoutNodes.forEach(n => { n.x += offsetX; n.y += offsetY; });
  for (const [id, pos] of nodePositions) {
    pos.x += offsetX;
    pos.y += offsetY;
  }

  // Compute edges
  const layoutEdges: LayoutEdge[] = [];
  for (const node of nodes) {
    for (const dep of node.dependsOn) {
      const from = nodePositions.get(dep);
      const to = nodePositions.get(node.id);
      if (from && to) {
        layoutEdges.push({
          from: dep,
          to: node.id,
          fromX: from.x + NODE_WIDTH / 2,
          fromY: from.y + NODE_HEIGHT,
          toX: to.x + NODE_WIDTH / 2,
          toY: to.y,
        });
      }
    }
  }

  const totalWidth = Math.max(...layoutNodes.map(n => n.x + NODE_WIDTH)) + 20;
  const totalHeight = Math.max(...layoutNodes.map(n => n.y + NODE_HEIGHT)) + 20;

  return { nodes: layoutNodes, edges: layoutEdges, width: totalWidth, height: totalHeight };
}
