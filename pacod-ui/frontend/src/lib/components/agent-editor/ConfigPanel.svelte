<script lang="ts">
	/** Settings of the selected component: on/off, remove, and its config form. */
	import { Trash2 } from 'lucide-svelte';
	import { m } from '$lib/paraglide/messages';
	import Button from '$lib/components/ui/Button.svelte';
	import type {
		AgentGraph,
		Effective,
		GraphNode,
		Issue,
		Registry,
		Template
	} from '$lib/features/agent-editor/queries';
	import {
		canRemove,
		effectiveValue,
		fieldsOf,
		nodeTypeOf
	} from '$lib/features/agent-editor/graph';
	import ConfigField from './ConfigField.svelte';
	import AssistantBox from './AssistantBox.svelte';
	import { TEXT_KINDS } from './labels';
	import { categoryLabel, nodeDescription, nodeLabel } from './labels';

	interface Props {
		profileId: string;
		graph: AgentGraph;
		node: GraphNode;
		registry: Registry;
		template: Template;
		/** Effective config with NO version applied — what a field inherits. */
		base: Effective | null;
		issues: Issue[];
		readonly: boolean;
		onfield: (field: string, value: unknown) => void;
		ontoggle: () => void;
		onremove: () => void;
	}

	let {
		profileId,
		graph,
		node,
		registry,
		template,
		base,
		issues,
		readonly,
		onfield,
		ontoggle,
		onremove
	}: Props = $props();

	const nt = $derived(nodeTypeOf(registry, node.type));
	const fields = $derived(nt ? fieldsOf(nt.config_schema) : []);
	// What the AI assistant can write: prompts and other text content.
	const textFields = $derived(fields.filter((f) => TEXT_KINDS.includes(f.kind)));
	let assistTarget = $state('');
	let assistBox = $state<HTMLDivElement | null>(null);
	function assistField(name: string) {
		assistTarget = name;
		assistBox?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
	}

	function fieldIssue(name: string): string | null {
		const hit = issues.find((i) => i.path.endsWith(`/config/${name}`));
		return hit ? hit.message : null;
	}
	const nodeLevel = $derived(
		issues.filter((i) => !fields.some((f) => i.path.endsWith(`/config/${f.name}`)))
	);
</script>

{#if nt}
	<div class="space-y-3">
		<div>
			<div
				class="text-[10px] font-semibold tracking-wide uppercase"
				style="color: var(--color-text-faint);"
			>
				{categoryLabel(nt.category)}
			</div>
			<h2 class="text-sm font-semibold" style="color: var(--color-text);">{nodeLabel(nt)}</h2>
			<p class="mt-0.5 text-xs" style="color: var(--color-text-muted);">{nodeDescription(nt)}</p>
		</div>

		<div class="flex items-center gap-2">
			{#if nt.toggleable}
				<label class="flex items-center gap-2 text-xs" style="color: var(--color-text);">
					<input type="checkbox" checked={node.enabled} disabled={readonly} onchange={ontoggle} />
					{m.agent_editor_node_enabled()}
				</label>
			{:else}
				<span class="text-xs" style="color: var(--color-text-faint);"
					>{m.agent_editor_node_required()}</span
				>
			{/if}
			<span class="flex-1"></span>
			{#if !readonly && canRemove(template, node)}
				<Button variant="danger-ghost" size="xs" onclick={onremove}>
					<Trash2 size={12} />{m.agent_editor_node_remove()}
				</Button>
			{/if}
		</div>

		{#if nt.tool_names.length}
			<p class="text-[11px]" style="color: var(--color-text-muted);">
				{m.agent_editor_node_tools()}
				{#each nt.tool_names as t (t)}
					<code class="ml-1 rounded px-1" style="background: var(--color-surface-3);">{t}</code>
				{/each}
			</p>
		{/if}

		{#each nodeLevel as issue, i (i)}
			<p
				class="rounded px-2 py-1 text-[11px]"
				style="background: {issue.severity === 'error'
					? 'var(--color-danger-soft)'
					: 'var(--color-warning-soft)'};
					color: {issue.severity === 'error' ? 'var(--color-danger)' : 'var(--color-text)'};"
			>
				{issue.message}
			</p>
		{/each}

		{#if textFields.length}
			<div bind:this={assistBox}>
				<AssistantBox
					{profileId}
					nodeId={node.id}
					{graph}
					fields={textFields}
					bind:target={assistTarget}
					{readonly}
					onapply={(name, value) => onfield(name, value)}
				/>
			</div>
		{/if}

		{#if fields.length === 0}
			<p class="text-xs" style="color: var(--color-text-faint);">
				{m.agent_editor_node_no_settings()}
			</p>
		{:else}
			<div class="space-y-2">
				{#each fields as field (field.name)}
					<ConfigField
						{field}
						value={node.config[field.name]}
						inherited={effectiveValue(base, field.target)}
						models={registry.models}
						retired={registry.retired_models}
						prompt={field.promptKey ? (registry.prompts[field.promptKey] ?? null) : null}
						{readonly}
						issue={fieldIssue(field.name)}
						onchange={(v) => onfield(field.name, v)}
						onassist={TEXT_KINDS.includes(field.kind) && !readonly
							? () => assistField(field.name)
							: undefined}
					/>
				{/each}
			</div>
		{/if}
	</div>
{/if}
