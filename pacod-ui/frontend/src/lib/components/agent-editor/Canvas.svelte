<script lang="ts">
	/**
	 * The agent graph on a Svelte Flow canvas.
	 *
	 * Phase 1 (template graphs): positions and edges come from the template's
	 * slot order (`arrange` in graph.ts), so nodes are not draggable and cannot
	 * be wired by hand. A component dragged from the palette can be dropped
	 * ANYWHERE on the canvas — its type determines its slot.
	 */
	import { Background, Controls, SvelteFlow, type Edge, type Node } from '@xyflow/svelte';
	import '@xyflow/svelte/dist/style.css';
	import { theme } from '$lib/stores/theme.svelte';
	import { m } from '$lib/paraglide/messages';
	import type { AgentGraph, Issue, Registry, Template } from '$lib/features/agent-editor/queries';
	import { issuesByNode, nodeTypeOf } from '$lib/features/agent-editor/graph';
	import { DRAG_MIME, nodeLabel } from './labels';
	import NodeCard, { type NodeCardData } from './NodeCard.svelte';

	interface Props {
		graph: AgentGraph;
		registry: Registry;
		template: Template;
		issues: Issue[];
		selectedId: string | null;
		onselect: (id: string | null) => void;
		ontoggle: (id: string) => void;
		ondroptype: (type: string) => void;
	}

	let { graph, registry, template, issues, selectedId, onselect, ontoggle, ondroptype }: Props =
		$props();

	const nodeTypes = { agent: NodeCard };

	function chipFor(config: Record<string, unknown>): string | null {
		for (const key of ['primary_model', 'planner_model', 'classifier_model']) {
			const v = config[key];
			if (typeof v === 'string' && v) return v;
		}
		return null;
	}

	let nodes = $state.raw<Node[]>([]);
	let edges = $state.raw<Edge[]>([]);

	$effect(() => {
		const byNode = issuesByNode(issues);
		const toolSlot = template.slots.find((s) => s.id === template.tool_slot);
		const parentSlot = template.slots.find((s) => s.id === template.tool_parent);
		nodes = graph.nodes.map((n) => {
			const nt = nodeTypeOf(registry, n.type);
			const nodeIssues = byNode.get(n.id) ?? [];
			const data: NodeCardData = {
				label: nt ? nodeLabel(nt) : n.type,
				category: nt?.category ?? '',
				enabled: n.enabled,
				toggleable: nt?.toggleable ?? false,
				selected: n.id === selectedId,
				errors: nodeIssues.filter((i) => i.severity === 'error').length,
				warnings: nodeIssues.filter((i) => i.severity === 'warning').length,
				chip: chipFor(n.config),
				isTool: !!toolSlot?.allowed.includes(n.type),
				isToolParent: !!parentSlot?.allowed.includes(n.type),
				onToggle: () => ontoggle(n.id)
			};
			return {
				id: n.id,
				type: 'agent',
				position: n.position,
				data,
				draggable: false,
				connectable: false,
				deletable: false
			};
		});
		edges = graph.edges.map((e) => ({
			id: e.id,
			source: e.source,
			target: e.target,
			sourceHandle: e.kind === 'tool' ? 'tools' : undefined,
			type: e.kind === 'tool' ? 'smoothstep' : 'default',
			animated: e.kind === 'tool',
			deletable: false,
			selectable: false
		}));
	});

	let dragOver = $state(false);

	function accepts(e: DragEvent): boolean {
		return !!e.dataTransfer?.types.includes(DRAG_MIME);
	}
</script>

<div
	class="relative h-full w-full rounded-lg border transition-colors"
	style="border-color: {dragOver ? 'var(--color-brand)' : 'var(--color-border)'};
		background: var(--color-surface);"
	role="region"
	aria-label={m.agent_editor_canvas_label()}
	ondragover={(e) => {
		if (!accepts(e)) return;
		e.preventDefault();
		dragOver = true;
	}}
	ondragleave={() => (dragOver = false)}
	ondrop={(e) => {
		dragOver = false;
		const type = e.dataTransfer?.getData(DRAG_MIME);
		if (type) {
			e.preventDefault();
			ondroptype(type);
		}
	}}
>
	<SvelteFlow
		bind:nodes
		bind:edges
		{nodeTypes}
		fitView
		nodesDraggable={false}
		nodesConnectable={false}
		deleteKey={null}
		colorMode={theme.current}
		minZoom={0.3}
		maxZoom={1.5}
		onnodeclick={({ node }) => onselect(node.id)}
		onpaneclick={() => onselect(null)}
		proOptions={{ hideAttribution: true }}
	>
		<Background />
		<Controls showLock={false} />
	</SvelteFlow>
	{#if dragOver}
		<div
			class="pointer-events-none absolute inset-0 flex items-center justify-center rounded-lg text-sm font-medium"
			style="background: color-mix(in srgb, var(--color-brand) 8%, transparent); color: var(--color-brand);"
		>
			{m.agent_editor_drop_hint()}
		</div>
	{/if}
</div>
