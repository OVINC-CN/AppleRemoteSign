import type { NotaryDetails, NotaryTicket } from './types';

export const nodeWidth = 236;
export const nodeHeight = 76;
const columnGap = 48;
const rowGap = 28;
const padding = 20;

export interface NotarySignature extends Omit<NotaryTicket, 'path'> {
  count: number;
}
export interface NotaryNode {
  id: string;
  name: string;
  path: string;
  kind: 'archive' | 'bundle' | 'directory' | 'code';
  signatures: NotarySignature[];
  children: NotaryNode[];
  depth: number;
  x: number;
  y: number;
}
export interface NotaryEdge {
  source: string;
  target: string;
  line: string;
}

export function buildNotaryTopology(details: NotaryDetails) {
  const root: NotaryNode = {
    id: 'archive',
    name: details.archiveFilename,
    path: details.archiveFilename,
    kind: 'archive',
    signatures: [],
    children: [],
    depth: 0,
    x: padding,
    y: padding,
  };
  const paths = new Map<string, NotaryNode>([
    [
      root.path,
      root,
    ],
  ]);
  for (const ticket of details.ticketContents || []) {
    const segments = ticket.path.split('/');
    let parent = root;
    const start = segments[0] === root.path ? 1 : 0;
    for (let index = start; index < segments.length; index += 1) {
      const path = segments.slice(0, index + 1).join('/');
      let node = paths.get(path);
      if (!node) {
        node = {
          id: `path:${path}`,
          name: segments[index] || '/',
          path,
          kind: 'directory',
          signatures: [],
          children: [],
          depth: parent.depth + 1,
          x: padding + (parent.depth + 1) * (nodeWidth + columnGap),
          y: padding,
        };
        paths.set(path, node);
        parent.children.push(node);
      }
      parent = node;
    }
    const signature = parent.signatures.find(
      (value) =>
        value.arch === (ticket.arch ?? null) &&
        value.cdhash === ticket.cdhash &&
        value.digestAlgorithm === ticket.digestAlgorithm,
    );
    if (signature) {
      signature.count += 1;
    } else {
      parent.signatures.push({
        arch: ticket.arch ?? null,
        cdhash: ticket.cdhash,
        digestAlgorithm: ticket.digestAlgorithm,
        count: 1,
      });
    }
  }
  const nodes: NotaryNode[] = [];
  const edges: NotaryEdge[] = [];
  let row = 0;
  // An explicit stack also handles very deep bundle paths without recursion.
  const stack: Array<{
    node: NotaryNode;
    placed: boolean;
  }> = [
    {
      node: root,
      placed: false,
    },
  ];
  while (stack.length) {
    const entry = stack.pop();
    if (!entry) {
      break;
    }
    const { node, placed } = entry;
    if (!placed) {
      nodes.push(node);
      node.children.sort((left, right) =>
        left.path < right.path ? -1 : left.path > right.path ? 1 : 0,
      );
      if (node !== root) {
        node.kind = /\.(app|framework|bundle|xpc)$/i.test(node.name)
          ? 'bundle'
          : node.signatures.length
            ? 'code'
            : 'directory';
      }
      stack.push({
        node,
        placed: true,
      });
      for (let index = node.children.length - 1; index >= 0; index -= 1) {
        stack.push({
          node: node.children[index],
          placed: false,
        });
      }
      continue;
    }
    node.y = node.children.length
      ? (node.children[0].y + node.children[node.children.length - 1].y) / 2
      : padding + row++ * (nodeHeight + rowGap);
    for (const child of node.children) {
      const fromX = node.x + nodeWidth;
      const fromY = node.y + nodeHeight / 2;
      const toY = child.y + nodeHeight / 2;
      edges.push({
        source: node.id,
        target: child.id,
        line: `M ${fromX} ${fromY} C ${fromX + columnGap / 2} ${fromY}, ${child.x - columnGap / 2} ${toY}, ${child.x} ${toY}`,
      });
    }
  }
  return {
    nodes,
    edges,
    width:
      nodes.reduce((maximum, node) => Math.max(maximum, node.x), 0) +
      nodeWidth +
      padding,
    height: Math.max(row, 1) * (nodeHeight + rowGap) - rowGap + padding * 2,
    signedPaths: nodes.filter((node) => node.signatures.length).length,
  };
}
