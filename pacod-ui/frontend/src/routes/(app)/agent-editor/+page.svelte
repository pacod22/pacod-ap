<script lang="ts">
	import { browser } from '$app/environment';
	import { untrack } from 'svelte';
	import { goto } from '$app/navigation';
	import { Waypoints, Plus, Download, ShieldCheck, Pencil, Sparkles } from 'lucide-svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import { m } from '$lib/paraglide/messages';
	import {
		agentsQuery,
		bulkActionMutation,
		createAgentMutation,
		editorError,
		editorKeys,
		importAgent,
		type AgentSummary
	} from '$lib/features/agent-editor/queries';
	import { useQueryClient } from '@tanstack/svelte-query';

	let { data } = $props();

	const agentsQ = agentsQuery(untrack(() => data.agents));
	const createM = createAgentMutation();
	const bulkM = bulkActionMutation();
	const agents = $derived<AgentSummary[]>($agentsQ.data ?? data.agents);

	let dialog = $state<HTMLDialogElement | null>(null);
	let form = $state({ slug: '', name: '', template: 'rag_chat_v1' });
	let formError = $state<string | null>(null);
	let notice = $state<string | null>(null);

	function templateLabel(id: string | null): string {
		return data.registry.templates.find((t) => t.id === id)?.label ?? '—';
	}

	async function create(e: SubmitEvent) {
		e.preventDefault();
		formError = null;
		try {
			const agent = await $createM.mutateAsync({ ...form });
			dialog?.close();
			await goto(`/agent-editor/${agent.profile_id}`);
		} catch (err) {
			formError = editorError(err).message;
		}
	}

	async function bulk(action: 'import-all' | 'adopt-all') {
		if (!browser) return;
		if (action === 'adopt-all' && !confirm(m.agent_editor_adopt_confirm())) return;
		try {
			const res = await $bulkM.mutateAsync(action);
			notice =
				action === 'adopt-all'
					? m.agent_editor_adopted({ count: res.adopted ?? 0 })
					: m.agent_editor_imported({ count: res.created ?? 0 });
		} catch (err) {
			notice = editorError(err).message;
		}
	}

	// Import one not-yet-imported agent (the row's own action).
	const qc = useQueryClient();
	async function importOne(id: string) {
		try {
			await importAgent(id);
			await qc.invalidateQueries({ queryKey: editorKeys.agents() });
			await goto(`/agent-editor/${id}`);
		} catch (err) {
			notice = editorError(err).message;
		}
	}

	function stateBadge(a: AgentSummary): { text: string; bg: string; fg: string } {
		if (a.release_state === 'archived')
			return {
				text: m.agent_editor_state_archived(),
				bg: 'var(--color-surface-3)',
				fg: 'var(--color-text-faint)'
			};
		if (a.release_state === 'dev')
			return {
				text: m.agent_editor_state_dev(),
				bg: 'var(--color-warning-soft)',
				fg: 'var(--color-warning)'
			};
		return {
			text: m.agent_editor_state_prod(),
			bg: 'var(--color-success-soft)',
			fg: 'var(--color-success)'
		};
	}
</script>

<div class="mx-auto max-w-6xl">
	<div class="mb-4 flex flex-wrap items-center gap-2">
		<Waypoints size={20} style="color: var(--color-brand);" />
		<h1 class="flex-1 text-lg font-semibold" style="color: var(--color-text);">
			{m.agent_editor_heading()}
		</h1>
		<Button variant="ghost" size="sm" onclick={() => goto('/agent-editor/assistant')}>
			<Sparkles size={14} />{m.agent_editor_ai_settings_title()}
		</Button>
		<Button
			variant="ghost"
			size="sm"
			onclick={() => bulk('import-all')}
			disabled={$bulkM.isPending}
		>
			<Download size={14} />{m.agent_editor_import_all()}
		</Button>
		<Button
			variant="secondary"
			size="sm"
			onclick={() => bulk('adopt-all')}
			disabled={$bulkM.isPending}
		>
			<ShieldCheck size={14} />{m.agent_editor_adopt_all()}
		</Button>
		<Button
			size="sm"
			onclick={() => {
				form = { slug: '', name: '', template: 'rag_chat_v1' };
				formError = null;
				dialog?.showModal();
			}}
		>
			<Plus size={14} />{m.agent_editor_new()}
		</Button>
	</div>

	<p class="mb-3 text-xs" style="color: var(--color-text-muted);">{m.agent_editor_intro()}</p>

	{#if notice}
		<p
			class="mb-3 rounded-md px-3 py-2 text-xs"
			style="background: var(--color-brand-soft); color: var(--color-text);"
		>
			{notice}
		</p>
	{/if}

	<div class="overflow-hidden rounded-lg border" style="border-color: var(--color-border);">
		<table class="data-table w-full text-sm">
			<thead>
				<tr>
					<th>{m.agent_editor_col_agent()}</th>
					<th>{m.agent_editor_col_template()}</th>
					<th>{m.agent_editor_col_state()}</th>
					<th>{m.agent_editor_col_production()}</th>
					<th>{m.agent_editor_col_dev()}</th>
					<th>{m.agent_editor_col_draft()}</th>
					<th></th>
				</tr>
			</thead>
			<tbody>
				{#each agents as a (a.profile_id)}
					{@const badge = stateBadge(a)}
					<tr>
						<td>
							<div class="font-medium" style="color: var(--color-text);">{a.name}</div>
							<div class="font-mono text-xs" style="color: var(--color-text-faint);">
								{a.profile_id}
							</div>
						</td>
						<td class="text-xs">{templateLabel(a.template)}</td>
						<td>
							<span
								class="rounded-full px-2 py-0.5 text-[11px] font-semibold"
								style="background: {badge.bg}; color: {badge.fg};"
							>
								{badge.text}
							</span>
						</td>
						<td class="text-xs">
							{#if a.published}v{a.published.version_no}{:else}<span
									style="color: var(--color-text-faint);">{m.agent_editor_legacy()}</span
								>{/if}
						</td>
						<td class="text-xs">{a.dev ? `v${a.dev.version_no}` : '—'}</td>
						<td class="text-xs">
							{#if !a.has_definition}
								<span style="color: var(--color-text-faint);">{m.agent_editor_not_imported()}</span>
							{:else if a.draft_dirty}
								<span style="color: var(--color-warning);">{m.agent_editor_draft_changed()}</span>
							{:else}
								<span style="color: var(--color-text-faint);">{m.agent_editor_draft_clean()}</span>
							{/if}
						</td>
						<td class="text-right">
							{#if a.has_definition}
								<Button
									variant="secondary"
									size="xs"
									onclick={() => goto(`/agent-editor/${a.profile_id}`)}
								>
									<Pencil size={12} />{m.agent_editor_open()}
								</Button>
							{:else}
								<Button variant="secondary" size="xs" onclick={() => importOne(a.profile_id)}>
									<Download size={12} />{m.agent_editor_import()}
								</Button>
							{/if}
						</td>
					</tr>
				{:else}
					<tr
						><td
							colspan="7"
							class="py-8 text-center text-xs"
							style="color: var(--color-text-muted);">{m.agent_editor_empty()}</td
						></tr
					>
				{/each}
			</tbody>
		</table>
	</div>
</div>

<dialog
	bind:this={dialog}
	class="w-full max-w-md rounded-lg border p-0 backdrop:bg-black/40"
	style="border-color: var(--color-border); background: var(--color-surface-2);"
>
	<form class="space-y-3 p-5" onsubmit={create}>
		<h2 class="text-base font-semibold" style="color: var(--color-text);">
			{m.agent_editor_new_title()}
		</h2>
		<label class="block">
			<span class="field-label">{m.agent_editor_new_name()}</span>
			<input class="field-input w-full" required maxlength="128" bind:value={form.name} />
		</label>
		<label class="block">
			<span class="field-label">{m.agent_editor_new_slug()}</span>
			<input
				class="field-input w-full font-mono"
				required
				minlength="2"
				maxlength="64"
				pattern="[a-z0-9][a-z0-9-]*[a-z0-9]"
				bind:value={form.slug}
			/>
			<span class="text-[11px]" style="color: var(--color-text-faint);"
				>{m.agent_editor_new_slug_hint()}</span
			>
		</label>
		<fieldset>
			<legend class="field-label">{m.agent_editor_new_template()}</legend>
			{#each data.registry.templates as t (t.id)}
				<label class="flex items-center gap-2 py-1 text-sm" style="color: var(--color-text);">
					<input type="radio" name="template" value={t.id} bind:group={form.template} />
					{t.kind === 'chat' ? m.agent_editor_template_chat() : t.label}
				</label>
			{/each}
		</fieldset>
		<p class="text-[11px]" style="color: var(--color-text-faint);">{m.agent_editor_new_note()}</p>
		{#if formError}
			<p class="text-xs" style="color: var(--color-danger);">{formError}</p>
		{/if}
		<div class="flex justify-end gap-2">
			<Button variant="ghost" type="button" onclick={() => dialog?.close()}
				>{m.common_cancel()}</Button
			>
			<Button type="submit" disabled={$createM.isPending}>{m.common_create()}</Button>
		</div>
	</form>
</dialog>
