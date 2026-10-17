<script lang="ts">
	import { Pencil, Eye, RotateCcw, X, Lock } from 'lucide-svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import {
		createPromptListQuery,
		updatePromptMutation,
		resetPromptMutation,
		type Prompt
	} from '$lib/features/agent-prompts/queries';
	import { selectedProfile } from '$lib/stores/selectedProfile.svelte';
	import { m } from '$lib/paraglide/messages';

	let { data } = $props();
	const canEdit = $derived(data.canEdit);

	const prompts = $derived(createPromptListQuery(selectedProfile.id));
	const rows = $derived([...($prompts.data ?? [])]);

	const updateMut = $derived(updatePromptMutation(selectedProfile.id));
	const resetMut = $derived(resetPromptMutation(selectedProfile.id));

	let dialog = $state<HTMLDialogElement | null>(null);
	let active = $state<Prompt | null>(null);
	let draft = $state('');
	let showDefault = $state(false);
	let formError = $state('');
	let confirmReset = $state(false);

	function open(p: Prompt) {
		active = p;
		draft = p.content;
		showDefault = false;
		formError = '';
		confirmReset = false;
		dialog?.showModal();
	}

	function close() {
		dialog?.close();
		active = null;
		formError = '';
		confirmReset = false;
	}

	const isDirty = $derived(active !== null && draft !== active.content);

	async function save() {
		if (!active) return;
		formError = '';
		try {
			await $updateMut.mutateAsync({ key: active.key, content: draft });
			close();
		} catch (e: unknown) {
			formError = e instanceof Error ? e.message : m.common_unexpected_error();
		}
	}

	async function reset() {
		if (!active) return;
		formError = '';
		try {
			const updated = await $resetMut.mutateAsync(active.key);
			active = updated;
			draft = updated.content;
			confirmReset = false;
		} catch (e: unknown) {
			formError = e instanceof Error ? e.message : m.common_unexpected_error();
		}
	}
</script>

<svelte:head><title>{m.agent_prompts_title()}</title></svelte:head>

<div class="space-y-6">
	<div class="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between sm:gap-4">
		<div>
			<h1 class="text-2xl font-semibold tracking-tight">{m.agent_prompts_heading()}</h1>
			<p class="mt-1 max-w-2xl text-sm text-[var(--color-text-muted)]">
				{m.agent_prompts_intro()}
				{#if canEdit}
					{m.agent_prompts_can_edit()}
				{:else}
					{m.agent_prompts_readonly_note()}
				{/if}
			</p>
		</div>
		{#if !canEdit}
			<span
				class="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-xs font-medium"
				style="background: var(--color-surface-3); color: var(--color-text-muted);"
			>
				<Lock size={13} />
				{m.agent_prompts_readonly_badge()}
			</span>
		{/if}
	</div>

	<div class="overflow-hidden rounded-xl border" style="border-color: var(--color-border);">
		<div class="overflow-x-auto">
			<table class="data-table data-table-stack">
				<thead>
					<tr>
						<th>{m.agent_prompts_col_phase()}</th>
						<th>{m.agent_prompts_col_prompt()}</th>
						<th>{m.common_lang()}</th>
						<th>{m.common_status()}</th>
						<th></th>
					</tr>
				</thead>
				<tbody>
					{#if $prompts.isPending}
						<tr
							><td colspan="5" class="py-12 text-center text-sm text-[var(--color-text-muted)]"
								>{m.agent_prompts_loading()}</td
							></tr
						>
					{:else if $prompts.isError}
						<tr
							><td colspan="5" class="py-12 text-center text-sm" style="color: var(--color-danger);"
								>{m.agent_prompts_load_failed()}</td
							></tr
						>
					{:else if rows.length > 0}
						{#each rows as p (p.key)}
							<tr>
								<td data-label={m.agent_prompts_col_phase()} class="text-[var(--color-text-muted)]"
									>{p.phase}</td
								>
								<td data-label={m.agent_prompts_col_prompt()} class="stack-block">
									<div class="font-medium">{p.label}</div>
									<div
										class="max-w-[420px] truncate text-xs text-[var(--color-text-muted)]"
										title={p.description}
									>
										{p.description}
									</div>
								</td>
								<td
									data-label={m.common_lang()}
									class="font-mono text-xs text-[var(--color-text-muted)] uppercase"
									>{p.language}</td
								>
								<td data-label={m.common_status()}>
									{#if p.managed}
										<span
											class="rounded-md px-1.5 py-0.5 text-xs font-medium"
											style="background: var(--color-warning-soft); color: var(--color-warning);"
											title={m.agent_editor_managed_notice()}>{m.agent_editor_managed_badge()}</span
										>
									{:else if p.is_overridden}
										<span
											class="rounded-md px-1.5 py-0.5 text-xs font-medium"
											style="background: var(--color-brand-soft); color: var(--color-brand);"
											>{m.agent_prompts_status_customised()}</span
										>
									{:else}
										<span
											class="rounded-md px-1.5 py-0.5 text-xs font-medium"
											style="background: var(--color-surface-3); color: var(--color-text-muted);"
											>{m.agent_prompts_status_default()}</span
										>
									{/if}
								</td>
								<td data-label="" class="w-16">
									<button
										onclick={() => open(p)}
										class="touch-target rounded-lg p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-3)] hover:text-[var(--color-text)]"
										aria-label={canEdit ? m.common_edit() : m.common_view()}
										title={canEdit ? m.common_edit() : m.common_view()}
									>
										{#if canEdit}<Pencil size={14} />{:else}<Eye size={14} />{/if}
									</button>
								</td>
							</tr>
						{/each}
					{:else}
						<tr
							><td colspan="5" class="py-12 text-center text-sm text-[var(--color-text-muted)]"
								>{m.agent_prompts_empty()}</td
							></tr
						>
					{/if}
				</tbody>
			</table>
		</div>
	</div>
</div>

<!-- View / edit dialog -->
<dialog
	bind:this={dialog}
	class="mx-auto max-h-[90dvh] w-[calc(100%-2rem)] max-w-3xl overflow-y-auto rounded-2xl p-0 shadow-2xl"
	style="background: var(--color-surface); border: 1px solid var(--color-border);"
>
	{#if active}
		<div
			class="flex items-center justify-between border-b px-6 py-4"
			style="border-color: var(--color-border);"
		>
			<div>
				<h2 class="text-base font-semibold">{active.label}</h2>
				<p class="text-xs text-[var(--color-text-muted)]">{active.phase} · {active.language}</p>
			</div>
			<button
				type="button"
				onclick={close}
				class="rounded-lg p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-2)] hover:text-[var(--color-text)]"
				aria-label={m.common_close()}><X size={16} /></button
			>
		</div>

		<div class="space-y-4 px-6 py-5">
			<p class="text-sm text-[var(--color-text-muted)]">{active.description}</p>

			{#if active.required_tokens.length > 0}
				<div class="flex flex-wrap items-center gap-2 text-xs">
					<span class="text-[var(--color-text-muted)]"
						>{m.agent_prompts_required_placeholders()}</span
					>
					{#each active.required_tokens as tok}
						<code class="rounded bg-[var(--color-surface-3)] px-1.5 py-0.5 font-mono">{tok}</code>
					{/each}
				</div>
			{/if}

			<div class="flex items-center justify-between">
				<label class="field-label !mb-0" for="agent-prompt-text"
					>{showDefault
						? m.agent_prompts_default_readonly()
						: m.agent_prompts_content_label()}</label
				>
				<button
					type="button"
					onclick={() => (showDefault = !showDefault)}
					class="text-xs font-medium text-[var(--color-brand)] hover:underline"
				>
					{showDefault ? m.agent_prompts_show_current() : m.agent_prompts_view_default()}
				</button>
			</div>

			{#if showDefault}
				<textarea
					id="agent-prompt-text"
					readonly
					rows={18}
					value={active.default_text}
					class="field-textarea font-mono text-xs"></textarea>
			{:else}
				<textarea
					id="agent-prompt-text"
					bind:value={draft}
					readonly={!canEdit || active.managed}
					rows={18}
					class="field-textarea font-mono text-xs"></textarea>
			{/if}

			{#if active.managed}
				<p class="text-xs" style="color: var(--color-warning);">
					{m.agent_editor_managed_notice()}
					<a href="/agent-editor" class="underline">{m.agent_editor_managed_link()}</a>
				</p>
			{:else if active.is_overridden && active.updated_at}
				<p class="text-xs text-[var(--color-text-muted)]">
					{m.agent_prompts_customised_by({
						who: active.updated_by ?? m.agent_prompts_unknown_user(),
						when: new Date(active.updated_at).toLocaleString()
					})}
				</p>
			{/if}
		</div>

		{#if formError}
			<div
				class="mx-6 mb-2 rounded-lg px-3 py-2 text-sm"
				style="background: var(--color-danger-soft); color: var(--color-danger);"
			>
				{formError}
			</div>
		{/if}

		<div
			class="flex items-center justify-between gap-2 border-t px-6 py-4"
			style="border-color: var(--color-border);"
		>
			<div>
				{#if canEdit && !active.managed && active.is_overridden}
					{#if confirmReset}
						<div class="flex items-center gap-2">
							<span class="text-xs text-[var(--color-text-muted)]"
								>{m.agent_prompts_reset_confirm()}</span
							>
							<Button
								type="button"
								variant="secondary"
								size="sm"
								onclick={reset}
								disabled={$resetMut.isPending}>{m.common_confirm()}</Button
							>
							<Button type="button" variant="ghost" size="sm" onclick={() => (confirmReset = false)}
								>{m.common_cancel()}</Button
							>
						</div>
					{:else}
						<Button type="button" variant="ghost" size="sm" onclick={() => (confirmReset = true)}>
							<RotateCcw size={14} />
							{m.agent_prompts_reset_default()}
						</Button>
					{/if}
				{/if}
			</div>
			<div class="flex gap-2">
				<Button type="button" variant="secondary" size="sm" onclick={close}
					>{canEdit ? m.common_cancel() : m.common_close()}</Button
				>
				{#if canEdit && !active.managed}
					<Button
						type="button"
						size="sm"
						onclick={save}
						disabled={!isDirty || showDefault || $updateMut.isPending}
					>
						{m.common_save_changes()}
					</Button>
				{/if}
			</div>
		</div>
	{/if}
</dialog>
