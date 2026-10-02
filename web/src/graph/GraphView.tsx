import { useMemo } from "react";
import {
  BaseEdge,
  Controls,
  Handle,
  Position,
  ReactFlow,
  getBezierPath,
  type Edge,
  type EdgeProps,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import type { GraphDef, GraphId, NodeState, ReplayState } from "../replay/types";
import { LAYOUT } from "./layout";

/** What Task 9 passes: the graph, its id, the player's ReplayState (from `stateAt`), role -> model, a click handler. */
export interface GraphViewProps {
  graph: GraphDef;
  graphId: GraphId;
  state: ReplayState;
  models: Record<string, string>;
  onNodeClick(nodeId: string): void;
}

interface FlowNodeData extends Record<string, unknown> {
  id: string;
  state: NodeState;
  visits: number;
  model: string | null;
}

interface FlowEdgeData extends Record<string, unknown> {
  from: string;
  to: string;
  route: string | null;
  taken: boolean;
}

type FlowNode = Node<FlowNodeData>;
type FlowEdge = Edge<FlowEdgeData>;

const SIZE: Record<string, { w: number; h: number }> = {
  startNode: { w: 56, h: 56 },
  agentNode: { w: 150, h: 84 },
  functionNode: { w: 140, h: 56 },
  routerNode: { w: 110, h: 72 },
};

const KIND_TYPE = {
  start: "startNode",
  llm: "agentNode",
  function: "functionNode",
  router: "routerNode",
} as const;

const STATE_CLASS: Record<NodeState, string> = {
  idle: "border-slate-300 bg-white text-slate-500 dark:border-slate-600 dark:bg-slate-900 dark:text-slate-400",
  active:
    "border-sky-500 bg-sky-50 text-sky-900 ring-2 ring-sky-400 motion-safe:animate-pulse dark:bg-sky-950 dark:text-sky-100",
  done: "border-emerald-500 bg-emerald-50 text-emerald-900 dark:bg-emerald-950 dark:text-emerald-100",
  stopped: "border-amber-500 bg-amber-50 text-amber-900 ring-2 ring-amber-400 dark:bg-amber-950 dark:text-amber-100",
  end: "border-violet-500 bg-violet-50 text-violet-900 dark:bg-violet-950 dark:text-violet-100",
};

const handleStyle = { opacity: 0, pointerEvents: "none" } as const;

function Handles() {
  return (
    <>
      <Handle id="l" type="target" position={Position.Left} style={handleStyle} isConnectable={false} />
      <Handle id="r" type="source" position={Position.Right} style={handleStyle} isConnectable={false} />
      <Handle id="tt" type="target" position={Position.Top} style={handleStyle} isConnectable={false} />
      <Handle id="ts" type="source" position={Position.Top} style={handleStyle} isConnectable={false} />
      <Handle id="bt" type="target" position={Position.Bottom} style={handleStyle} isConnectable={false} />
      <Handle id="bs" type="source" position={Position.Bottom} style={handleStyle} isConnectable={false} />
    </>
  );
}

function Visits({ n }: { n: number }) {
  return n >= 2 ? <span className="ml-1 font-semibold">{`×${n}`}</span> : null;
}

function StartNode({ data }: NodeProps<FlowNode>) {
  return (
    <div
      data-testid={`node-${data.id}`}
      data-state={data.state}
      className="flex flex-col items-center text-xs"
    >
      <div className={`h-4 w-4 rounded-full border-2 ${STATE_CLASS[data.state]}`} />
      <span className="mt-1 text-slate-700 dark:text-slate-200">issue</span>
      <span className="text-[10px] text-slate-500 dark:text-slate-400">{data.state}</span>
      <Handles />
    </div>
  );
}

function AgentNode({ data }: NodeProps<FlowNode>) {
  return (
    <div
      data-testid={`node-${data.id}`}
      data-state={data.state}
      className={`flex h-[84px] w-[150px] flex-col items-center justify-center rounded-xl border-2 px-2 text-center ${STATE_CLASS[data.state]}`}
    >
      <span className="text-sm font-semibold">
        {data.id}
        <Visits n={data.visits} />
      </span>
      {data.model ? <span className="text-[11px] opacity-80">{data.model}</span> : null}
      <span className="mt-0.5 text-[10px] uppercase tracking-wide">{data.state}</span>
      <Handles />
    </div>
  );
}

function FunctionNode({ data }: NodeProps<FlowNode>) {
  return (
    <div
      data-testid={`node-${data.id}`}
      data-state={data.state}
      className={`flex h-[56px] w-[140px] flex-col items-center justify-center rounded-md border-2 px-2 text-center ${STATE_CLASS[data.state]}`}
    >
      <span className="text-xs font-medium">
        {data.id}
        <Visits n={data.visits} />
      </span>
      <span className="text-[10px] uppercase tracking-wide">{data.state}</span>
      <Handles />
    </div>
  );
}

function RouterNode({ data }: NodeProps<FlowNode>) {
  return (
    <div
      data-testid={`node-${data.id}`}
      data-state={data.state}
      className="relative flex h-[72px] w-[110px] items-center justify-center text-center"
    >
      <div className={`h-9 w-9 rotate-45 rounded-lg border-2 ${STATE_CLASS[data.state]}`} />
      <div className="absolute left-0 right-0 top-full flex flex-col items-center">
        <span className="mt-1 text-[11px] font-medium text-slate-700 dark:text-slate-200">
          {data.id}
          <Visits n={data.visits} />
        </span>
        <span className="text-[10px] uppercase tracking-wide text-slate-500 dark:text-slate-400">{data.state}</span>
      </div>
      <Handles />
    </div>
  );
}

function RouteEdge({
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  data,
}: EdgeProps<FlowEdge>) {
  const [path, labelX, labelY] = getBezierPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
  });
  const taken = data?.taken ?? false;
  const route = data?.route ?? null;
  return (
    <g data-testid={`edge-${data?.from}-${data?.to}`} data-taken={taken ? "true" : "false"}>
      <BaseEdge
        path={path}
        style={
          taken
            ? { stroke: "#0284c7", strokeWidth: 2.5 }
            : { stroke: "#94a3b8", strokeWidth: 1.5, strokeDasharray: "6 5", opacity: 0.55 }
        }
        markerEnd="url(#replay-arrow)"
      />
      {taken && route ? (
        <text
          x={labelX}
          y={labelY - 6}
          textAnchor="middle"
          className="fill-sky-800 text-[11px] font-semibold dark:fill-sky-200"
        >
          {route}
        </text>
      ) : null}
    </g>
  );
}

const nodeTypes = {
  startNode: StartNode,
  agentNode: AgentNode,
  functionNode: FunctionNode,
  routerNode: RouterNode,
};
const edgeTypes = { routeEdge: RouteEdge };

/** Edges into a node below the main path drop from the bottom handle, whatever the x order; back edges curve above. */
export function handlesFor(from: { x: number; y: number }, to: { x: number; y: number }) {
  if (to.y > from.y + 50) return { sourceHandle: "bs", targetHandle: "tt" };
  if (to.x < from.x) return { sourceHandle: "ts", targetHandle: "tt" };
  return { sourceHandle: "r", targetHandle: "l" };
}

export function GraphView({ graph, graphId, state, models, onNodeClick }: GraphViewProps) {
  const layout = LAYOUT[graphId];
  const nodes = useMemo<FlowNode[]>(
    () =>
      graph.nodes.map((n) => {
        const type = KIND_TYPE[n.kind];
        const size = SIZE[type];
        return {
          id: n.id,
          type,
          position: layout[n.id],
          initialWidth: size.w,
          initialHeight: size.h,
          data: {
            id: n.id,
            state: state.nodeStates[n.id] ?? "idle",
            visits: state.visits[n.id] ?? 0,
            model: n.kind === "llm" ? (models[n.id] ?? null) : null,
          },
        };
      }),
    [graph, layout, state.nodeStates, state.visits, models],
  );
  const edges = useMemo<FlowEdge[]>(
    () =>
      graph.edges.map((e) => {
        const taken = state.takenEdges.some((t) => t.from === e.from && t.to === e.to);
        return {
          id: `edge-${e.from}-${e.to}`,
          source: e.from,
          target: e.to,
          type: "routeEdge",
          ...handlesFor(layout[e.from], layout[e.to]),
          data: { from: e.from, to: e.to, route: e.route, taken },
        };
      }),
    [graph, layout, state.takenEdges],
  );

  return (
    <div className="relative h-full w-full">
      <svg width="0" height="0" aria-hidden="true" className="absolute">
        <defs>
          <marker id="replay-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">
            <path d="M0,0 L10,5 L0,10 z" fill="#64748b" />
          </marker>
        </defs>
      </svg>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        nodeOrigin={[0.5, 0.5]}
        fitView
        colorMode="system"
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={false}
        zoomOnScroll={false}
        proOptions={{ hideAttribution: true }}
        onNodeClick={(_, node) => onNodeClick(node.id)}
      >
        <Controls showInteractive={false} />
      </ReactFlow>
      <div className="pointer-events-none absolute bottom-2 right-2 flex items-center gap-2 rounded bg-white/80 px-2 py-1 text-xs text-slate-600 dark:bg-slate-900/80 dark:text-slate-300">
        <svg width="28" height="6" aria-hidden="true">
          <line x1="0" y1="3" x2="28" y2="3" stroke="#94a3b8" strokeWidth="2" strokeDasharray="6 5" />
        </svg>
        <span>dashed: not taken in this run</span>
      </div>
    </div>
  );
}
