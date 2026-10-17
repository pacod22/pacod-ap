<script lang="ts" module>
	/** What the canvas hands each node card (built in Canvas.svelte). */
	export interface NodeCardData extends Record<string, unknown> {
		label: string;
		category: string;
		enabled: boolean;
		toggleable: boolean;
		selected: boolean;
		errors: number;
		warnings: number;
		/** Short summary shown under the label (e.g. the model id). */
		chip: string | null;
		isTool: boolean;
		isToolParent: boolean;
		onToggle: () => void;
	}

	// Category → accent colour (CSS tokens with literal fallbacks).
	export const CATEGORY_COLORS: Record<string, string> = {
		input: '#64748b',
		guard: '#b45309',
		cache: '#0891b2',
		memory: '#7c3aed',
		plan: '#2563eb',
		tool: '#0d9488',
		generate: 'var(--color-brand)',
		post: '#db2777',
		output: '#64748b'
	};
</script>

<script lang="ts">
	import { Handle, Position } from '@xyflow/svelte';
	import { CircleAlert, TriangleAlert } from 'lucide-svelte';
	import { m } from '$lib/paraglide/messages';
	import { categoryLabel } from './labels';

	let { data }: { data: NodeCardData } = $props();

	const accent = $derived(CATEGORY_COLORS[data.category] ?? 'var(--color-text-muted)');
</script>

<div
	class="w-[200px] rounded-lg border bg-[var(--color-surface-2)] text-left shadow-sm transition-shadow"
	class:opacity-50={!data.enabled}
	style="border-color: {data.selected ? 'var(--color-brand)' : 'var(--color-border-strong)'};
		box-shadow: {data.selected ? '0 0 0 2px var(--color-brand-soft)' : ''};
		border-top: 3px solid {accent};"
>
	{#if data.isTool}
		<Handle type="target" position={Position.Left} isConnectable={false} />
	{:else}
		<Handle type="target" position={Position.Top} isConnectable={false} />
	{/if}

	<div class="flex items-start gap-2 px-3 py-2">
		<div class="min-w-0 flex-1">
			<div class="truncate text-xs font-semibold" style="color: var(--color-text);">
				{data.label}
			</div>
			{#if data.chip}
				<div class="truncate font-mono text-[10px]" style="color: var(--color-text-muted);">
					{data.chip}
				</div>
			{/if}
			<div class="mt-0.5 text-[10px] tracking-wide uppercase" style="color: {accent};">
				{categoryLabel(data.category)}
			</div>
		</div>
		<div class="flex flex-col items-end gap-1">
			{#if data.toggleable}
				<!-- Its own click target: toggling must not also select-and-open the node. -->
				<button
					type="button"
					class="nodrag relative h-4 w-7 rounded-full transition-colors"
					style="background: {data.enabled ? 'var(--color-brand)' : 'var(--color-border-strong)'};"
					title={data.enabled ? m.agent_editor_node_disable() : m.agent_editor_node_enable()}
					aria-pressed={data.enabled}
					onclick={(e) => {
						e.stopPropagation();
						data.onToggle();
					}}
				>
					<span
						class="absolute top-0.5 h-3 w-3 rounded-full bg-white transition-all"
						style="left: {data.enabled ? '0.875rem' : '0.125rem'};"
					></span>
				</button>
			{/if}
			{#if data.errors}
				<span
					class="flex items-center gap-0.5 text-[10px] font-semibold"
					style="color: var(--color-danger);"
					title={m.agent_editor_errors_count({ count: data.errors })}
				>
					<CircleAlert size={11} />{data.errors}
				</span>
			{:else if data.warnings}
				<span
					class="flex items-center gap-0.5 text-[10px] font-semibold"
					style="color: var(--color-warning);"
					title={m.agent_editor_warnings_count({ count: data.warnings })}
				>
					<TriangleAlert size={11} />{data.warnings}
				</span>
			{/if}
		</div>
	</div>

	{#if !data.isTool}
		<Handle type="source" position={Position.Bottom} isConnectable={false} />
	{/if}
	{#if data.isToolParent}
		<Handle id="tools" type="source" position={Position.Right} isConnectable={false} />
	{/if}
</div>
