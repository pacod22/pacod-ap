<script lang="ts">
	/**
	 * Add / edit one LLM template. The parameter fields follow the chosen model's
	 * capabilities (from the backend's `model_capabilities`, the same function the
	 * request builder uses), so the form never offers a knob the model rejects.
	 */
	import { Save, X } from 'lucide-svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import { m } from '$lib/paraglide/messages';
	import {
		createTemplateMutation,
		stripUnsupported,
		templateErrorMessage,
		updateTemplateMutation,
		visibleParams,
		type LlmTemplate,
		type ModelCapabilities,
		type TemplateInput
	} from '$lib/features/llm-config/templates';

	let {
		models,
		capabilities
	}: { models: string[]; capabilities: Record<string, ModelCapabilities> } = $props();

	const createMut = createTemplateMutation();
	const updateMut = updateTemplateMutation();

	let dialog = $state<HTMLDialogElement | null>(null);
	let editing = $state<LlmTemplate | null>(null);
	let formError = $state('');

	let fId = $state('');
	let fName = $state('');
	let fDescription = $state('');
	let fModel = $state('');
	let fMax = $state(4096);
	let fTempOn = $state(false);
	let fTemp = $state(0.2);
	let fThinking = $state(0);
	let fEffort = $state('');
	let fTopP = $state<number | null>(null);
	let fFreq = $state<number | null>(null);
	let fPres = $state<number | null>(null);
	let fTimeout = $state<number | null>(null);

	const caps = $derived(capabilities[fModel]);
	const show = $derived(visibleParams(caps));
	const modelOptions = $derived(fModel && !models.includes(fModel) ? [fModel, ...models] : models);

	/** Open the dialog: a template to edit, a template to copy (`copy`), or blank. */
	export function open(t: LlmTemplate | null = null, copy = false) {
		editing = t && !copy ? t : null;
		formError = '';
		fId = '';
		fName = t ? (copy ? `${t.name} (${m.llmtpl_copy_suffix()})` : t.name) : '';
		fDescription = t?.description ?? '';
		fModel = t?.model ?? models[0] ?? '';
		fMax = t?.max_output_tokens ?? 4096;
		fTempOn = t ? t.temperature !== null : true;
		fTemp = t?.temperature ?? 0.2;
		fThinking = t?.thinking_budget_tokens ?? 0;
		fEffort = t?.reasoning_effort ?? '';
		fTopP = t?.top_p ?? null;
		fFreq = t?.frequency_penalty ?? null;
		fPres = t?.presence_penalty ?? null;
		fTimeout = t?.timeout_seconds ?? null;
		dialog?.showModal();
	}

	// `bind:value` on an empty number input yields null/'' — normalise to null.
	const num = (v: number | string | null): number | null =>
		v === null || v === '' || Number.isNaN(Number(v)) ? null : Number(v);

	async function save() {
		formError = '';
		const input: TemplateInput = stripUnsupported(
			{
				name: fName.trim(),
				description: fDescription.trim(),
				model: fModel,
				max_output_tokens: Number(fMax),
				temperature: fTempOn ? Number(fTemp) : null,
				thinking_budget_tokens: Number(fThinking) || 0,
				reasoning_effort: fEffort || null,
				top_p: num(fTopP),
				frequency_penalty: num(fFreq),
				presence_penalty: num(fPres),
				timeout_seconds: num(fTimeout)
			},
			caps
		);
		try {
			if (editing) {
				await $updateMut.mutateAsync({ id: editing.id, patch: input });
			} else {
				await $createMut.mutateAsync({ ...input, id: fId.trim() || undefined });
			}
			dialog?.close();
		} catch (e) {
			formError = templateErrorMessage(e, m.common_unexpected_error());
		}
	}
</script>

<dialog
	bind:this={dialog}
	class="m-auto w-full max-w-2xl rounded-xl p-0 backdrop:bg-black/40"
	style="background: var(--color-surface); color: var(--color-text); border: 1px solid var(--color-border);"
>
	<div
		class="flex items-center justify-between border-b px-6 py-4"
		style="border-color: var(--color-border);"
	>
		<h2 class="text-base font-semibold">
			{editing ? m.llmtpl_edit_title() : m.llmtpl_add_title()}
		</h2>
		<button
			onclick={() => dialog?.close()}
			class="rounded-lg p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-2)]"
			aria-label={m.common_close()}><X size={16} /></button
		>
	</div>

	<div class="max-h-[70vh] space-y-4 overflow-y-auto px-6 py-5">
		{#if editing && editing.usages.length > 0}
			<p
				class="rounded-lg px-3 py-2 text-xs"
				style="background: var(--color-warning-soft); color: var(--color-warning);"
			>
				{m.llmtpl_live_warning({ count: editing.usages.length })}
			</p>
		{/if}

		<div class="grid grid-cols-1 gap-3 sm:grid-cols-2">
			<div>
				<label class="field-label" for="tpl-name">{m.llmtpl_field_name()}</label>
				<input id="tpl-name" bind:value={fName} maxlength="160" class="field-input" />
			</div>
			{#if editing}
				<div>
					<span class="field-label">{m.llmtpl_field_id()}</span>
					<p class="pt-2 font-mono text-xs text-[var(--color-text-muted)]">{editing.id}</p>
				</div>
			{:else}
				<div>
					<label class="field-label" for="tpl-id">{m.llmtpl_field_id()}</label>
					<input
						id="tpl-id"
						bind:value={fId}
						maxlength="64"
						placeholder="fast-classifier"
						class="field-input font-mono text-xs"
					/>
					<p class="mt-1 text-xs text-[var(--color-text-muted)]">{m.llmtpl_field_id_hint()}</p>
				</div>
			{/if}
		</div>

		<div>
			<label class="field-label" for="tpl-desc">{m.llmtpl_field_description()}</label>
			<input id="tpl-desc" bind:value={fDescription} maxlength="2000" class="field-input" />
		</div>

		<div class="grid grid-cols-1 gap-3 sm:grid-cols-2">
			<div>
				<label class="field-label" for="tpl-model">{m.llmtpl_field_model()}</label>
				<select id="tpl-model" bind:value={fModel} class="field-input">
					{#each modelOptions as mdl (mdl)}<option value={mdl}>{mdl}</option>{/each}
				</select>
			</div>
			<div>
				<label class="field-label" for="tpl-max">{m.llmtpl_field_max_tokens()}</label>
				<input
					id="tpl-max"
					type="number"
					min="1"
					max="200000"
					step="1"
					bind:value={fMax}
					class="field-input"
				/>
			</div>
		</div>

		<div class="grid grid-cols-1 gap-3 sm:grid-cols-2">
			{#if show.temperature}
				<div>
					<label class="field-label" for="tpl-temp">{m.llmtpl_field_temperature()}</label>
					<input
						id="tpl-temp"
						type="number"
						step="0.05"
						min="0"
						max="2"
						bind:value={fTemp}
						disabled={!fTempOn}
						class="field-input disabled:opacity-50"
					/>
					<label class="mt-1 flex items-center gap-1.5 text-xs text-[var(--color-text-muted)]">
						<input type="checkbox" bind:checked={fTempOn} />
						{m.llmtpl_send_temperature()}
					</label>
				</div>
			{/if}
			<div>
				<label class="field-label" for="tpl-think">{m.llmtpl_field_thinking()}</label>
				<input
					id="tpl-think"
					type="number"
					min="0"
					max="200000"
					step="1"
					bind:value={fThinking}
					class="field-input"
				/>
				<p class="mt-1 text-xs text-[var(--color-text-muted)]">
					{#if caps?.thinking === 'adaptive'}
						{m.llmtpl_thinking_hint_adaptive()}
					{:else if caps?.thinking === 'budget'}
						{m.llmtpl_thinking_hint_budget({ min: caps.min_thinking_budget })}
					{:else}
						{m.llmtpl_thinking_hint_effort()}
					{/if}
				</p>
			</div>
			{#if show.effort}
				<div>
					<label class="field-label" for="tpl-effort">{m.llmtpl_field_effort()}</label>
					<select
						id="tpl-effort"
						bind:value={fEffort}
						class="field-input disabled:opacity-50"
						disabled={!(Number(fThinking) > 0)}
					>
						<option value="">{m.llmtpl_effort_default()}</option>
						{#each caps?.effort_levels ?? ['low', 'medium', 'high', 'xhigh', 'max'] as lvl (lvl)}
							<option value={lvl}>{lvl}</option>
						{/each}
					</select>
					<p class="mt-1 text-xs text-[var(--color-text-muted)]">{m.llmtpl_effort_hint()}</p>
				</div>
			{/if}
			{#if show.top_p}
				<div>
					<label class="field-label" for="tpl-topp">{m.llmtpl_field_top_p()}</label>
					<input
						id="tpl-topp"
						type="number"
						step="0.01"
						min="0"
						max="1"
						bind:value={fTopP}
						class="field-input"
					/>
					<p class="mt-1 text-xs text-[var(--color-text-muted)]">{m.llmtpl_optional_hint()}</p>
				</div>
			{/if}
			{#if show.penalties}
				<div>
					<label class="field-label" for="tpl-freq">{m.llmtpl_field_frequency_penalty()}</label>
					<input
						id="tpl-freq"
						type="number"
						step="0.1"
						min="-2"
						max="2"
						bind:value={fFreq}
						class="field-input"
					/>
					<p class="mt-1 text-xs text-[var(--color-text-muted)]">{m.llmtpl_optional_hint()}</p>
				</div>
				<div>
					<label class="field-label" for="tpl-pres">{m.llmtpl_field_presence_penalty()}</label>
					<input
						id="tpl-pres"
						type="number"
						step="0.1"
						min="-2"
						max="2"
						bind:value={fPres}
						class="field-input"
					/>
					<p class="mt-1 text-xs text-[var(--color-text-muted)]">{m.llmtpl_optional_hint()}</p>
				</div>
			{/if}
			<div>
				<label class="field-label" for="tpl-timeout">{m.llmtpl_field_timeout()}</label>
				<input
					id="tpl-timeout"
					type="number"
					step="1"
					min="1"
					max="600"
					bind:value={fTimeout}
					class="field-input"
				/>
				<p class="mt-1 text-xs text-[var(--color-text-muted)]">{m.llmtpl_timeout_hint()}</p>
			</div>
		</div>

		{#if formError}<p class="text-sm" style="color: var(--color-danger);">{formError}</p>{/if}
	</div>

	<div class="flex justify-end gap-3 border-t px-6 py-4" style="border-color: var(--color-border);">
		<Button variant="secondary" size="sm" onclick={() => dialog?.close()}
			>{m.common_cancel()}</Button
		>
		<Button
			size="sm"
			onclick={save}
			disabled={!fName.trim() || !fModel || $createMut.isPending || $updateMut.isPending}
			><Save size={14} /> {m.common_save()}</Button
		>
	</div>
</dialog>
