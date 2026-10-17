<script lang="ts">
	/**
	 * The component's AI assistant: the author describes what they want, the
	 * assistant drafts the component's text fields (prompts, scope texts, …).
	 * Suggestions are validated server-side and NEVER applied automatically —
	 * the author reviews each one and applies it into the draft.
	 */
	import { Sparkles, Check, Settings2 } from 'lucide-svelte';
	import { m } from '$lib/paraglide/messages';
	import Button from '$lib/components/ui/Button.svelte';
	import {
		assistComponent,
		editorError,
		type AgentGraph,
		type Suggestion
	} from '$lib/features/agent-editor/queries';
	import type { FieldSpec } from '$lib/features/agent-editor/graph';

	interface Props {
		profileId: string;
		nodeId: string;
		graph: AgentGraph;
		/** The component's text fields the assistant can write. */
		fields: FieldSpec[];
		/** Field pre-selected via a field's sparkle button ('' = all). */
		target: string;
		readonly: boolean;
		onapply: (field: string, value: unknown) => void;
	}

	let {
		profileId,
		nodeId,
		graph,
		fields,
		target = $bindable(''),
		readonly,
		onapply
	}: Props = $props();

	let instruction = $state('');
	let busy = $state(false);
	let error = $state<string | null>(null);
	let result = $state<Suggestion | null>(null);
	let applied = $state<Set<string>>(new Set());

	// A different component → a fresh conversation.
	$effect(() => {
		void nodeId;
		result = null;
		error = null;
		applied = new Set();
	});

	const titleOf = (name: string) => fields.find((f) => f.name === name)?.title ?? name;

	async function generate() {
		busy = true;
		error = null;
		result = null;
		applied = new Set();
		try {
			result = await assistComponent(profileId, {
				graph: $state.snapshot(graph) as AgentGraph,
				node_id: nodeId,
				field: target || null,
				instruction
			});
		} catch (e) {
			error = editorError(e).message;
		} finally {
			busy = false;
		}
	}

	function apply(name: string) {
		if (!result) return;
		onapply(name, result.fields[name]);
		applied = new Set([...applied, name]);
	}

	function preview(v: unknown): string {
		return Array.isArray(v) ? v.join('\n') : String(v ?? '');
	}
</script>

<div
	class="rounded-md border p-2.5"
	style="border-color: color-mix(in srgb, var(--color-brand) 40%, var(--color-border));
		background: color-mix(in srgb, var(--color-brand) 4%, transparent);"
>
	<div class="mb-2 flex items-center gap-1.5">
		<Sparkles size={14} style="color: var(--color-brand);" />
		<span class="flex-1 text-xs font-semibold" style="color: var(--color-text);"
			>{m.agent_editor_ai_title()}</span
		>
		<a
			href="/agent-editor/assistant"
			class="rounded p-0.5 hover:bg-[var(--color-surface-3)]"
			title={m.agent_editor_ai_settings_link()}
		>
			<Settings2 size={13} style="color: var(--color-text-muted);" />
		</a>
	</div>

	<select class="field-input mb-1.5 w-full text-xs" bind:value={target} disabled={busy || readonly}>
		<option value="">{m.agent_editor_ai_all_fields()}</option>
		{#each fields as f (f.name)}
			<option value={f.name}>{f.title}</option>
		{/each}
	</select>
	<textarea
		class="field-textarea w-full text-xs"
		rows="3"
		placeholder={m.agent_editor_ai_placeholder()}
		bind:value={instruction}
		disabled={busy || readonly}></textarea>
	<div class="mt-1.5 flex items-center gap-2">
		<span class="flex-1 text-[10px]" style="color: var(--color-text-faint);"
			>{m.agent_editor_ai_hint()}</span
		>
		<Button size="xs" onclick={generate} disabled={busy || readonly}>
			<Sparkles size={12} />{busy ? m.agent_editor_ai_working() : m.agent_editor_ai_generate()}
		</Button>
	</div>

	{#if error}
		<p class="mt-2 text-[11px]" style="color: var(--color-danger);">{error}</p>
	{/if}

	{#if result}
		<div class="mt-2 space-y-2">
			{#if result.notes}
				<p class="text-[11px] italic" style="color: var(--color-text-muted);">{result.notes}</p>
			{/if}
			{#each Object.keys(result.fields) as name (name)}
				<div
					class="rounded border p-2"
					style="border-color: var(--color-border); background: var(--color-surface-2);"
				>
					<div class="mb-1 flex items-center gap-2">
						<span class="flex-1 text-[11px] font-medium" style="color: var(--color-text);"
							>{titleOf(name)}</span
						>
						{#if applied.has(name)}
							<span
								class="flex items-center gap-1 text-[11px]"
								style="color: var(--color-success);"
							>
								<Check size={11} />{m.agent_editor_ai_applied()}
							</span>
						{:else}
							<Button variant="secondary" size="xs" onclick={() => apply(name)} disabled={readonly}>
								{m.agent_editor_ai_apply()}
							</Button>
						{/if}
					</div>
					<pre
						class="max-h-48 overflow-auto font-mono text-[10px] whitespace-pre-wrap"
						style="color: var(--color-text-muted);">{preview(result.fields[name])}</pre>
				</div>
			{/each}
			{#if Object.keys(result.fields).length > 1}
				<Button
					variant="ghost"
					size="xs"
					disabled={readonly}
					onclick={() => Object.keys(result?.fields ?? {}).forEach(apply)}
				>
					<Check size={12} />{m.agent_editor_ai_apply_all()}
				</Button>
			{/if}
			{#each result.issues as issue (issue.field)}
				<p class="text-[11px]" style="color: var(--color-danger);">
					{m.agent_editor_ai_rejected({ field: titleOf(issue.field) })}: {issue.message}
				</p>
			{/each}
			{#if !Object.keys(result.fields).length && !result.issues.length}
				<p class="text-[11px]" style="color: var(--color-text-muted);">
					{m.agent_editor_ai_nothing()}
				</p>
			{/if}
			<p class="text-[10px]" style="color: var(--color-text-faint);">
				{m.agent_editor_ai_model({ model: result.model })}
			</p>
		</div>
	{/if}
</div>
