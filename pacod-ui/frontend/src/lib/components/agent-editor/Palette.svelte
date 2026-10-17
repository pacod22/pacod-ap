<script lang="ts">
	/**
	 * The component library: every node type of the agent's template, grouped by
	 * category. Drag one onto the canvas (or click "+") to add it to its slot;
	 * types already in the graph — and required ones — are shown as placed.
	 */
	import { Plus, Check, GripVertical } from 'lucide-svelte';
	import { m } from '$lib/paraglide/messages';
	import type { AgentGraph, Registry, Template } from '$lib/features/agent-editor/queries';
	import { canAdd } from '$lib/features/agent-editor/graph';
	import { CATEGORY_COLORS } from './NodeCard.svelte';
	import { DRAG_MIME, categoryLabel, nodeDescription, nodeLabel } from './labels';

	interface Props {
		graph: AgentGraph;
		registry: Registry;
		template: Template;
		readonly: boolean;
		onadd: (type: string) => void;
	}

	let { graph, registry, template, readonly, onadd }: Props = $props();

	// Template slot order, then grouped by category in that order.
	const groups = $derived.by(() => {
		const order = template.slots.flatMap((s) => s.allowed);
		const types = registry.node_types
			.filter((nt) => order.includes(nt.type))
			.sort((a, b) => order.indexOf(a.type) - order.indexOf(b.type));
		const out: { category: string; items: typeof types }[] = [];
		for (const nt of types) {
			const g = out.find((x) => x.category === nt.category);
			if (g) g.items.push(nt);
			else out.push({ category: nt.category, items: [nt] });
		}
		return out;
	});
</script>

<div class="flex h-full flex-col overflow-hidden">
	<div
		class="border-b px-3 py-2 text-xs font-semibold tracking-wide uppercase"
		style="border-color: var(--color-border); color: var(--color-text-muted);"
	>
		{m.agent_editor_palette_title()}
	</div>
	<div class="flex-1 space-y-3 overflow-y-auto p-2">
		{#each groups as group (group.category)}
			<div>
				<div
					class="mb-1 px-1 text-[10px] font-semibold tracking-wide uppercase"
					style="color: {CATEGORY_COLORS[group.category] ?? 'var(--color-text-muted)'};"
				>
					{categoryLabel(group.category)}
				</div>
				<div class="space-y-1">
					{#each group.items as nt (nt.type)}
						{@const placed = graph.nodes.some((n) => n.type === nt.type)}
						{@const addable = !readonly && canAdd(graph, template, nt.type)}
						<div
							class="group flex items-center gap-1.5 rounded-md border px-2 py-1.5 text-xs"
							class:cursor-grab={addable}
							style="border-color: var(--color-border); background: var(--color-surface-2);
								opacity: {placed || addable ? 1 : 0.55};"
							draggable={addable}
							role="listitem"
							title={nodeDescription(nt)}
							ondragstart={(e) => {
								if (!addable) return e.preventDefault();
								e.dataTransfer?.setData(DRAG_MIME, nt.type);
								if (e.dataTransfer) e.dataTransfer.effectAllowed = 'copy';
							}}
						>
							{#if addable}
								<GripVertical size={12} style="color: var(--color-text-faint);" />
							{/if}
							<span class="min-w-0 flex-1 truncate" style="color: var(--color-text);">
								{nodeLabel(nt)}
							</span>
							{#if placed}
								<Check size={13} style="color: var(--color-success);" />
							{:else if addable}
								<button
									type="button"
									class="rounded p-0.5 hover:bg-[var(--color-surface-3)]"
									title={m.agent_editor_palette_add()}
									onclick={() => onadd(nt.type)}
								>
									<Plus size={13} />
								</button>
							{/if}
						</div>
					{/each}
				</div>
			</div>
		{/each}
	</div>
	<p
		class="border-t px-3 py-2 text-[11px]"
		style="border-color: var(--color-border); color: var(--color-text-faint);"
	>
		{m.agent_editor_palette_hint()}
	</p>
</div>
