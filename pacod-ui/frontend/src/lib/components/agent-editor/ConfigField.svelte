<script lang="ts">
	/**
	 * One config field of a component. Every field is either INHERITED (no value
	 * in the graph — the agent uses the app_settings/env value shown greyed out)
	 * or OVERRIDDEN (the graph carries an explicit value). "Override" copies the
	 * inherited value in as the starting point; "Inherit" drops it again.
	 */
	import { RotateCcw, PenLine, Sparkles } from 'lucide-svelte';
	import { m } from '$lib/paraglide/messages';
	import type { FieldSpec } from '$lib/features/agent-editor/graph';
	import { missingTokens } from '$lib/features/agent-editor/graph';
	import type { PromptMeta } from '$lib/features/agent-editor/queries';

	interface Props {
		field: FieldSpec;
		/** Explicit value in the graph; `undefined` = inherited. */
		value: unknown;
		inherited: unknown;
		models: string[];
		retired: Record<string, string>;
		prompt: PromptMeta | null;
		readonly: boolean;
		issue: string | null;
		onchange: (value: unknown) => void;
		/** Present for text fields: target the AI assistant at this field. */
		onassist?: () => void;
	}

	let {
		field,
		value,
		inherited,
		models,
		retired,
		prompt,
		readonly,
		issue,
		onchange,
		onassist
	}: Props = $props();

	const overridden = $derived(value !== undefined);
	const shown = $derived(overridden ? value : inherited);
	let promptOpen = $state(false);

	function startOverride() {
		let v: unknown = inherited;
		if (v === undefined || v === null) {
			v =
				field.kind === 'boolean'
					? false
					: field.kind === 'number' || field.kind === 'integer'
						? (field.minimum ?? 0)
						: field.kind === 'multiselect' || field.kind === 'string_list'
							? []
							: field.kind === 'prompt'
								? (prompt?.default_text ?? '')
								: '';
		}
		onchange(Array.isArray(v) ? [...v] : v);
	}

	function display(v: unknown): string {
		if (v === undefined || v === null || v === '') return m.agent_editor_value_unset();
		if (Array.isArray(v)) return v.length ? v.join(', ') : m.agent_editor_value_unset();
		if (typeof v === 'boolean') return v ? m.common_on() : m.common_off();
		return String(v);
	}

	const missing = $derived(
		field.kind === 'prompt' && overridden && prompt
			? missingTokens(String(value ?? ''), prompt.required_tokens)
			: []
	);
	const modelOptions = $derived(
		typeof value === 'string' && value && !models.includes(value) ? [value, ...models] : models
	);
</script>

<div class="rounded-md border p-2.5" style="border-color: var(--color-border);">
	<div class="mb-1.5 flex items-center gap-2">
		<span class="flex-1 text-xs font-medium" style="color: var(--color-text);">{field.title}</span>
		{#if onassist}
			<button
				type="button"
				class="rounded p-0.5 hover:bg-[var(--color-surface-3)]"
				title={m.agent_editor_ai_for_field()}
				onclick={onassist}
			>
				<Sparkles size={12} style="color: var(--color-brand);" />
			</button>
		{/if}
		<span
			class="rounded-full px-1.5 py-0.5 text-[10px] font-medium"
			style="background: {overridden ? 'var(--color-brand-soft)' : 'var(--color-surface-3)'};
				color: {overridden ? 'var(--color-brand-muted)' : 'var(--color-text-faint)'};"
		>
			{overridden ? m.agent_editor_field_overridden() : m.agent_editor_field_inherited()}
		</span>
		{#if !readonly}
			{#if overridden}
				<button
					type="button"
					class="flex items-center gap-1 rounded px-1 text-[11px] hover:bg-[var(--color-surface-3)]"
					style="color: var(--color-text-muted);"
					title={m.agent_editor_field_inherit_title()}
					onclick={() => onchange(undefined)}
				>
					<RotateCcw size={11} />{m.agent_editor_field_inherit()}
				</button>
			{:else}
				<button
					type="button"
					class="flex items-center gap-1 rounded px-1 text-[11px] hover:bg-[var(--color-surface-3)]"
					style="color: var(--color-brand);"
					onclick={startOverride}
				>
					<PenLine size={11} />{m.agent_editor_field_override()}
				</button>
			{/if}
		{/if}
	</div>

	{#if !overridden || readonly}
		{#if field.kind === 'prompt'}
			<button
				type="button"
				class="w-full text-left text-[11px]"
				style="color: var(--color-text-muted);"
				onclick={() => (promptOpen = !promptOpen)}
			>
				{promptOpen ? m.agent_editor_prompt_hide() : m.agent_editor_prompt_show()}
			</button>
			{#if promptOpen}
				<pre
					class="mt-1 max-h-64 overflow-auto rounded p-2 font-mono text-[11px] whitespace-pre-wrap"
					style="background: var(--color-surface-3); color: var(--color-text-muted);">{String(
						shown ?? ''
					)}</pre>
			{/if}
		{:else}
			<div class="truncate font-mono text-xs" style="color: var(--color-text-faint);">
				{display(shown)}
			</div>
		{/if}
	{:else if field.kind === 'model'}
		<select
			class="field-input w-full text-xs"
			value={String(value ?? '')}
			onchange={(e) => onchange(e.currentTarget.value)}
		>
			{#if !field.minLength}
				<!-- blank = this role falls back to another model (planner → primary, …) -->
				<option value="">{m.agent_editor_model_fallback()}</option>
			{/if}
			{#each modelOptions as model (model)}
				<option value={model}>{model}</option>
			{/each}
		</select>
		{#if typeof value === 'string' && retired[value]}
			<p class="mt-1 text-[11px]" style="color: var(--color-warning);">
				{m.agent_editor_model_retired({ successor: retired[value] })}
			</p>
		{/if}
	{:else if field.kind === 'prompt'}
		<textarea
			class="field-textarea w-full font-mono text-[11px]"
			rows="14"
			value={String(value ?? '')}
			oninput={(e) => onchange(e.currentTarget.value)}></textarea>
		{#if prompt}
			<div class="mt-1 flex flex-wrap items-center gap-1 text-[10px]">
				{#each prompt.required_tokens as token (token)}
					<code
						class="rounded px-1"
						style="background: {missing.includes(token)
							? 'var(--color-danger-soft)'
							: 'var(--color-surface-3)'};
							color: {missing.includes(token) ? 'var(--color-danger)' : 'var(--color-text-muted)'};">{token}</code
					>
				{/each}
				{#if prompt.render === 'format'}
					<span style="color: var(--color-text-faint);">{m.agent_editor_prompt_braces()}</span>
				{/if}
				<button
					type="button"
					class="ml-auto underline"
					style="color: var(--color-text-muted);"
					onclick={() => onchange(prompt?.default_text ?? '')}
				>
					{m.agent_editor_prompt_load_default()}
				</button>
			</div>
		{/if}
	{:else if field.kind === 'textarea'}
		<textarea
			class="field-textarea w-full text-xs"
			rows="4"
			value={String(value ?? '')}
			oninput={(e) => onchange(e.currentTarget.value)}></textarea>
	{:else if field.kind === 'boolean'}
		<label class="flex items-center gap-2 text-xs" style="color: var(--color-text);">
			<input
				type="checkbox"
				checked={value === true}
				onchange={(e) => onchange(e.currentTarget.checked)}
			/>
			{value ? m.common_on() : m.common_off()}
		</label>
	{:else if field.kind === 'number' || field.kind === 'integer'}
		<input
			class="field-input w-full text-xs"
			type="number"
			min={field.minimum}
			max={field.maximum}
			step={field.kind === 'integer' ? 1 : 0.01}
			value={value as number}
			oninput={(e) => {
				const n = e.currentTarget.valueAsNumber;
				if (!Number.isNaN(n)) onchange(field.kind === 'integer' ? Math.round(n) : n);
			}}
		/>
	{:else if field.kind === 'select'}
		<select
			class="field-input w-full text-xs"
			value={String(value ?? '')}
			onchange={(e) => onchange(e.currentTarget.value)}
		>
			{#each field.options as opt (opt)}
				<option value={opt}>{opt}</option>
			{/each}
		</select>
	{:else if field.kind === 'multiselect'}
		<div class="flex flex-wrap gap-3">
			{#each field.options as opt (opt)}
				{@const list = Array.isArray(value) ? (value as string[]) : []}
				<label class="flex items-center gap-1 text-xs" style="color: var(--color-text);">
					<input
						type="checkbox"
						checked={list.includes(opt)}
						onchange={(e) =>
							onchange(
								e.currentTarget.checked
									? [...list, opt].filter((x, i, a) => a.indexOf(x) === i)
									: list.filter((x) => x !== opt)
							)}
					/>
					{opt}
				</label>
			{/each}
		</div>
	{:else if field.kind === 'string_list'}
		<textarea
			class="field-textarea w-full font-mono text-xs"
			rows="4"
			placeholder={m.agent_editor_list_placeholder()}
			value={Array.isArray(value) ? (value as string[]).join('\n') : ''}
			onchange={(e) =>
				onchange(
					e.currentTarget.value
						.split('\n')
						.map((s) => s.trim())
						.filter(Boolean)
				)}></textarea>
	{:else}
		<input
			class="field-input w-full text-xs"
			maxlength={field.maxLength}
			value={String(value ?? '')}
			oninput={(e) => onchange(e.currentTarget.value)}
		/>
	{/if}

	{#if issue}
		<p class="mt-1 text-[11px]" style="color: var(--color-danger);">{issue}</p>
	{/if}
</div>
