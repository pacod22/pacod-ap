<script lang="ts">
	/**
	 * Settings form of the agent editor's AI assistant LLM — ONE global config
	 * (GET/PATCH /api/v1/agent-editor/assistant-config, superuser-only). Shared by
	 * the editor's "AI assistant" page and the superuser section of LLM & Search.
	 * Mount it only for superusers: it fetches on mount.
	 */
	import { untrack } from 'svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import TemplateSelect from '$lib/components/llm-templates/TemplateSelect.svelte';
	import { createTemplatesQuery } from '$lib/features/llm-config/templates';
	import { m } from '$lib/paraglide/messages';
	import {
		assistantConfigQuery,
		editorError,
		updateAssistantConfigMutation,
		type AssistantState,
		type AssistantValues
	} from '$lib/features/agent-editor/queries';

	let { initial }: { initial?: AssistantState } = $props();

	const q = assistantConfigQuery(untrack(() => initial));
	// LLM templates (global): picking one replaces the model / temperature /
	// max-token / thinking fields below.
	const templatesQuery = createTemplatesQuery();
	const templates = $derived($templatesQuery.data?.templates ?? []);
	const save = updateAssistantConfigMutation();
	const cfg = $derived($q.data ?? initial ?? null);

	let form = $state<AssistantValues | null>(
		untrack(() => (initial ? { ...initial.effective } : null))
	);
	// Without an SSR snapshot, seed the form once the query arrives.
	$effect(() => {
		if (!form && $q.data) form = { ...$q.data.effective };
	});
	let message = $state<{ ok: boolean; text: string } | null>(null);

	const modelOptions = $derived(
		!cfg
			? []
			: form?.model && !cfg.available_models.includes(form.model)
				? [form.model, ...cfg.available_models]
				: cfg.available_models
	);

	async function submit(e: SubmitEvent) {
		e.preventDefault();
		if (!form) return;
		message = null;
		try {
			const res = await $save.mutateAsync({ ...form });
			form = { ...res.effective };
			message = { ok: true, text: m.common_saved() };
		} catch (err) {
			message = { ok: false, text: editorError(err).message };
		}
	}
</script>

{#if !form || !cfg}
	{#if $q.isError}
		<p class="text-sm" style="color: var(--color-danger);">{editorError($q.error).message}</p>
	{:else}
		<p class="text-sm" style="color: var(--color-text-muted);">{m.common_loading()}</p>
	{/if}
{:else}
	<form class="space-y-4" onsubmit={submit}>
		<label class="flex items-center gap-2 text-sm" style="color: var(--color-text);">
			<input type="checkbox" bind:checked={form.enabled} />
			{m.agent_editor_ai_enabled()}
		</label>

		<div>
			<TemplateSelect
				id="assistant-tpl"
				label={m.llmtpl_role_assistant()}
				bind:value={form.template}
				{templates}
				params={cfg.call_params}
			/>
			<span class="text-[11px]" style="color: var(--color-text-faint);"
				>{m.llmtpl_assistant_template_hint()}</span
			>
		</div>

		{#if !form.template}
			<label class="block">
				<span class="field-label">{m.agent_editor_ai_model_label()}</span>
				<select class="field-input w-full" bind:value={form.model}>
					<option value=""
						>{m.agent_editor_ai_model_default({ model: cfg.platform_default_model || '—' })}</option
					>
					{#each modelOptions as model (model)}
						<option value={model}>{model}</option>
					{/each}
				</select>
				<span class="text-[11px]" style="color: var(--color-text-faint);">
					{m.agent_editor_ai_model_effective({ model: cfg.effective_model })}
				</span>
			</label>

			<div class="grid gap-4 sm:grid-cols-2">
				<label class="block">
					<span class="field-label">{m.agent_editor_ai_temperature()}</span>
					<input
						class="field-input w-full"
						type="number"
						min="0"
						max="2"
						step="0.05"
						bind:value={form.temperature}
						disabled={!form.temperature_enabled}
					/>
					<span
						class="mt-1 flex items-center gap-1.5 text-[11px]"
						style="color: var(--color-text-muted);"
					>
						<input type="checkbox" bind:checked={form.temperature_enabled} />
						{m.agent_editor_ai_send_temperature()}
					</span>
				</label>
				<label class="block">
					<span class="field-label">{m.agent_editor_ai_max_tokens()}</span>
					<input
						class="field-input w-full"
						type="number"
						min="256"
						max="64000"
						step="1"
						bind:value={form.max_output_tokens}
					/>
				</label>
				<label class="block">
					<span class="field-label">{m.agent_editor_ai_thinking()}</span>
					<input
						class="field-input w-full"
						type="number"
						min="0"
						max="64000"
						step="1"
						bind:value={form.thinking_budget_tokens}
					/>
					<span class="text-[11px]" style="color: var(--color-text-faint);"
						>{m.agent_editor_ai_thinking_hint()}</span
					>
				</label>
			</div>
		{/if}

		<label class="block">
			<span class="field-label">{m.agent_editor_ai_guidelines()}</span>
			<textarea
				class="field-textarea w-full text-sm"
				rows="6"
				maxlength="8000"
				placeholder={m.agent_editor_ai_guidelines_placeholder()}
				bind:value={form.guidelines}></textarea>
			<span class="text-[11px]" style="color: var(--color-text-faint);"
				>{m.agent_editor_ai_guidelines_hint()}</span
			>
		</label>

		{#if message}
			<p
				class="text-xs"
				style="color: {message.ok ? 'var(--color-success)' : 'var(--color-danger)'};"
			>
				{message.text}
			</p>
		{/if}
		<div class="flex justify-end">
			<Button type="submit" disabled={$save.isPending}>{m.common_save()}</Button>
		</div>
	</form>
{/if}
