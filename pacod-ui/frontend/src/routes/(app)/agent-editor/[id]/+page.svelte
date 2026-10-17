<script lang="ts">
	/**
	 * Visual agent editor for one agent.
	 *
	 *   palette (component library) │ canvas (template pipeline) │ side panel
	 *
	 * The page edits a local copy of the DRAFT graph; "Save" stores it
	 * (optimistic revision check), "Deploy to dev" freezes it as the admin-only
	 * dev version, "Publish" releases it to production. Validation runs live
	 * (debounced) against the server so issues pin to the right node.
	 */
	import { beforeNavigate } from '$app/navigation';
	import { browser } from '$app/environment';
	import { onMount, untrack } from 'svelte';
	import {
		ArrowLeft,
		Save,
		FlaskConical,
		Rocket,
		CircleAlert,
		TriangleAlert,
		MoreHorizontal
	} from 'lucide-svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import { m } from '$lib/paraglide/messages';
	import Canvas from '$lib/components/agent-editor/Canvas.svelte';
	import Palette from '$lib/components/agent-editor/Palette.svelte';
	import ConfigPanel from '$lib/components/agent-editor/ConfigPanel.svelte';
	import AgentSettingsPanel from '$lib/components/agent-editor/AgentSettingsPanel.svelte';
	import MarketingEditor from '$lib/components/agent-editor/MarketingEditor.svelte';
	import VersionsPanel from '$lib/components/agent-editor/VersionsPanel.svelte';
	import TestChatPanel from '$lib/components/agent-editor/TestChatPanel.svelte';
	import {
		agentActionMutation,
		editorError,
		fetchEffective,
		saveDraftMutation,
		validateGraph,
		type AgentDetail,
		type AgentGraph,
		type Effective,
		type Issue
	} from '$lib/features/agent-editor/queries';
	import {
		addNode,
		arrange,
		graphKey,
		hasErrors,
		removeNode,
		setConfigField,
		templateOf,
		updateNode
	} from '$lib/features/agent-editor/graph';

	let { data } = $props();

	const registry = untrack(() => data.registry);
	const id = untrack(() => data.agent.profile_id);

	let agent = $state<AgentDetail>(untrack(() => data.agent));
	let graph = $state<AgentGraph>(structuredClone(untrack(() => data.agent.graph)));
	let savedKey = $state(graphKey(untrack(() => data.agent.graph)));
	let revision = $state(untrack(() => data.agent.draft_revision ?? 1));
	let issues = $state<Issue[]>(untrack(() => data.agent.issues));
	let selectedId = $state<string | null>(null);
	type Tab = 'component' | 'agent' | 'marketing' | 'versions' | 'test';
	let tab = $state<Tab>('agent');
	let base = $state<Effective | null>(null);
	let banner = $state<{ kind: 'error' | 'ok'; text: string } | null>(null);
	let busy = $state(false);
	let menuOpen = $state(false);

	const template = $derived(templateOf(registry, graph));
	const dirty = $derived(graphKey(graph) !== savedKey);
	const errors = $derived(issues.filter((i) => i.severity === 'error'));
	const warnings = $derived(issues.filter((i) => i.severity === 'warning'));
	const selected = $derived(graph.nodes.find((n) => n.id === selectedId) ?? null);
	const listedOnLanding = $derived(
		!!((graph.meta as Record<string, unknown>).marketing as Record<string, unknown> | null)
			?.show_on_landing
	);

	const saveM = saveDraftMutation(id);
	const actionM = agentActionMutation(id);

	onMount(async () => {
		try {
			base = await fetchEffective(id, 'base');
		} catch {
			base = null; // inherited values just won't be shown
		}
	});

	beforeNavigate(({ cancel }) => {
		if (dirty && browser && !confirm(m.agent_editor_leave_confirm())) cancel();
	});

	// Live validation, debounced, on every graph change.
	let validateTimer: ReturnType<typeof setTimeout> | undefined;
	$effect(() => {
		const snapshot = $state.snapshot(graph) as AgentGraph;
		graphKey(snapshot); // track deep changes
		clearTimeout(validateTimer);
		validateTimer = setTimeout(async () => {
			try {
				issues = await validateGraph(id, snapshot);
			} catch {
				/* keep the last known issues */
			}
		}, 600);
		return () => clearTimeout(validateTimer);
	});

	// ---- graph edits --------------------------------------------------------
	function select(nodeId: string | null) {
		selectedId = nodeId;
		if (nodeId) tab = 'component';
	}
	function add(type: string) {
		if (!template) return;
		graph = addNode(graph, template, type);
		const added = graph.nodes.find((n) => n.type === type);
		if (added) select(added.id);
	}
	function toggle(nodeId: string) {
		const node = graph.nodes.find((n) => n.id === nodeId);
		if (node) graph = updateNode(graph, nodeId, { enabled: !node.enabled });
	}
	function remove(nodeId: string) {
		if (!template) return;
		graph = removeNode(graph, template, nodeId);
		selectedId = null;
		tab = 'agent';
	}
	function setMeta(patch: Record<string, unknown>) {
		graph = { ...graph, meta: { ...graph.meta, ...patch } };
	}

	// ---- server actions -------------------------------------------------------
	function showError(e: unknown) {
		const err = editorError(e);
		if (err.issues.length) issues = err.issues;
		banner = { kind: 'error', text: err.message };
	}

	function adopt(detail: AgentDetail) {
		agent = detail;
		graph = structuredClone(detail.graph);
		savedKey = graphKey(detail.graph);
		revision = detail.draft_revision ?? revision;
		issues = detail.issues;
	}

	async function save(): Promise<boolean> {
		if (!dirty) return true;
		busy = true;
		try {
			const res = await $saveM.mutateAsync({
				graph: $state.snapshot(graph) as AgentGraph,
				revision
			});
			revision = res.revision;
			graph = template ? arrange(res.graph, template) : res.graph;
			savedKey = graphKey(res.graph);
			issues = res.issues;
			agent = { ...agent, draft_revision: res.revision, draft_dirty: true };
			banner = { kind: 'ok', text: m.agent_editor_saved() };
			return true;
		} catch (e) {
			showError(e);
			return false;
		} finally {
			busy = false;
		}
	}

	async function act(action: string, body?: unknown, okText?: string) {
		busy = true;
		try {
			adopt(await $actionM.mutateAsync({ action, body }));
			if (okText) banner = { kind: 'ok', text: okText };
		} catch (e) {
			showError(e);
		} finally {
			busy = false;
			menuOpen = false;
		}
	}

	async function deployDev() {
		if (!(await save())) return;
		await act('deploy-dev', undefined, m.agent_editor_deployed_dev());
	}

	let publishDialog = $state<HTMLDialogElement | null>(null);
	let publishNote = $state('');
	let assignAll = $state(false);
	async function publish(e: SubmitEvent) {
		e.preventDefault();
		publishDialog?.close();
		if (!(await save())) return;
		await act(
			'publish',
			{ note: publishNote || null, assign_to_all_users: assignAll },
			m.agent_editor_published()
		);
		publishNote = '';
	}

	async function rollback(versionId: number) {
		if (!confirm(m.agent_editor_rollback_confirm())) return;
		await act('rollback', { version_id: versionId }, m.agent_editor_rolled_back());
	}
	async function resetDraft(versionId: number) {
		if (dirty && !confirm(m.agent_editor_reset_draft_confirm())) return;
		await act('draft/reset', { version_id: versionId }, m.agent_editor_draft_reset_done());
	}
	async function setState(state: 'dev' | 'prod' | 'archived') {
		const msg =
			state === 'archived'
				? m.agent_editor_archive_confirm()
				: state === 'dev'
					? m.agent_editor_unpublish_confirm()
					: null;
		if (msg && !confirm(msg)) return;
		await act('release-state', { state });
	}

	const TABS: { id: Tab; label: () => string }[] = [
		{ id: 'component', label: m.agent_editor_tab_component },
		{ id: 'agent', label: m.agent_editor_tab_agent },
		{ id: 'marketing', label: m.agent_editor_tab_marketing },
		{ id: 'versions', label: m.agent_editor_tab_versions },
		{ id: 'test', label: m.agent_editor_tab_test }
	];
</script>

<div class="flex h-[calc(100dvh-5.5rem)] flex-col gap-2">
	<!-- Header / deploy bar -->
	<div class="flex flex-wrap items-center gap-2">
		<a
			href="/agent-editor"
			class="rounded p-1 hover:bg-[var(--color-surface-3)]"
			title={m.agent_editor_back()}
		>
			<ArrowLeft size={16} />
		</a>
		<div class="min-w-0">
			<h1 class="truncate text-base font-semibold" style="color: var(--color-text);">
				{String((graph.meta as Record<string, unknown>).name || agent.name)}
			</h1>
			<div
				class="flex flex-wrap items-center gap-1.5 text-[11px]"
				style="color: var(--color-text-muted);"
			>
				<span class="font-mono">{agent.profile_id}</span>
				<span>·</span>
				<span
					class="rounded-full px-1.5 font-semibold"
					style="background: {agent.release_state === 'prod'
						? 'var(--color-success-soft)'
						: agent.release_state === 'dev'
							? 'var(--color-warning-soft)'
							: 'var(--color-surface-3)'};"
				>
					{agent.release_state === 'prod'
						? m.agent_editor_state_prod()
						: agent.release_state === 'dev'
							? m.agent_editor_state_dev()
							: m.agent_editor_state_archived()}
				</span>
				<span
					>{m.agent_editor_prod_version({
						v: agent.published ? `v${agent.published.version_no}` : '—'
					})}</span
				>
				<span
					>{m.agent_editor_dev_version({ v: agent.dev ? `v${agent.dev.version_no}` : '—' })}</span
				>
				{#if dirty}<span style="color: var(--color-warning);">● {m.agent_editor_unsaved()}</span
					>{/if}
			</div>
		</div>
		<span class="flex-1"></span>
		{#if errors.length}
			<span class="flex items-center gap-1 text-xs" style="color: var(--color-danger);">
				<CircleAlert size={13} />{m.agent_editor_errors_count({ count: errors.length })}
			</span>
		{:else if warnings.length}
			<span class="flex items-center gap-1 text-xs" style="color: var(--color-warning);">
				<TriangleAlert size={13} />{m.agent_editor_warnings_count({ count: warnings.length })}
			</span>
		{/if}
		<Button variant="secondary" size="sm" disabled={!dirty || busy} onclick={save}>
			<Save size={13} />{m.common_save()}
		</Button>
		<Button
			variant="secondary"
			size="sm"
			disabled={busy || hasErrors(issues)}
			title={m.agent_editor_deploy_dev_title()}
			onclick={deployDev}
		>
			<FlaskConical size={13} />{m.agent_editor_deploy_dev()}
		</Button>
		<Button
			size="sm"
			disabled={busy || hasErrors(issues)}
			title={m.agent_editor_publish_title()}
			onclick={() => {
				// A landing-page agent links visitors into the chat: existing users
				// need access, or the card's CTA 403s for them.
				assignAll = listedOnLanding;
				publishDialog?.showModal();
			}}
		>
			<Rocket size={13} />{m.agent_editor_publish()}
		</Button>
		<div class="relative">
			<Button
				variant="ghost"
				size="icon"
				onclick={() => (menuOpen = !menuOpen)}
				title={m.agent_editor_more()}
			>
				<MoreHorizontal size={16} />
			</Button>
			{#if menuOpen}
				<div
					class="absolute right-0 z-20 mt-1 w-56 rounded-md border py-1 text-xs shadow-lg"
					style="border-color: var(--color-border); background: var(--color-surface-2);"
				>
					{#if agent.dev}
						<button
							class="block w-full px-3 py-1.5 text-left hover:bg-[var(--color-surface-3)]"
							onclick={() => act('undeploy-dev')}
						>
							{m.agent_editor_undeploy_dev()}
						</button>
					{/if}
					{#if agent.release_state === 'prod'}
						<button
							class="block w-full px-3 py-1.5 text-left hover:bg-[var(--color-surface-3)]"
							onclick={() => setState('dev')}
						>
							{m.agent_editor_unpublish()}
						</button>
					{:else if agent.published}
						<button
							class="block w-full px-3 py-1.5 text-left hover:bg-[var(--color-surface-3)]"
							onclick={() => setState('prod')}
						>
							{m.agent_editor_republish()}
						</button>
					{/if}
					{#if agent.release_state !== 'archived'}
						<button
							class="block w-full px-3 py-1.5 text-left hover:bg-[var(--color-surface-3)]"
							style="color: var(--color-danger);"
							onclick={() => setState('archived')}
						>
							{m.agent_editor_archive()}
						</button>
					{/if}
					<button
						class="block w-full px-3 py-1.5 text-left hover:bg-[var(--color-surface-3)]"
						onclick={() => {
							if (confirm(m.agent_editor_reimport_confirm()))
								act('import', { force: true }, m.agent_editor_reimported());
						}}
					>
						{m.agent_editor_reimport()}
					</button>
				</div>
			{/if}
		</div>
	</div>

	{#if banner}
		<div
			class="flex items-center gap-2 rounded-md px-3 py-1.5 text-xs"
			style="background: {banner.kind === 'error'
				? 'var(--color-danger-soft)'
				: 'var(--color-success-soft)'};
				color: {banner.kind === 'error' ? 'var(--color-danger)' : 'var(--color-success)'};"
		>
			<span class="flex-1">{banner.text}</span>
			<button class="text-[11px] underline" onclick={() => (banner = null)}
				>{m.common_close()}</button
			>
		</div>
	{/if}

	{#if !template}
		<p class="text-sm" style="color: var(--color-danger);">{m.agent_editor_no_template()}</p>
	{:else}
		<div class="flex min-h-0 flex-1 gap-2">
			<aside
				class="hidden w-56 shrink-0 overflow-hidden rounded-lg border lg:block"
				style="border-color: var(--color-border); background: var(--color-surface-2);"
			>
				<Palette {graph} {registry} {template} readonly={busy} onadd={add} />
			</aside>

			<main class="min-w-0 flex-1">
				<Canvas
					{graph}
					{registry}
					{template}
					{issues}
					{selectedId}
					onselect={select}
					ontoggle={toggle}
					ondroptype={add}
				/>
			</main>

			<aside
				class="flex w-[380px] shrink-0 flex-col overflow-hidden rounded-lg border"
				style="border-color: var(--color-border); background: var(--color-surface-2);"
			>
				<div class="flex border-b" style="border-color: var(--color-border);">
					{#each TABS as t (t.id)}
						<button
							type="button"
							class="-mb-px flex-1 border-b-2 px-1 py-2 text-[11px] font-medium"
							style="border-color: {tab === t.id ? 'var(--color-brand)' : 'transparent'};
								color: {tab === t.id ? 'var(--color-brand)' : 'var(--color-text-muted)'};"
							disabled={t.id === 'component' && !selected}
							onclick={() => (tab = t.id)}
						>
							{t.label()}
						</button>
					{/each}
				</div>
				<div class="min-h-0 flex-1 overflow-y-auto p-3">
					{#if tab === 'component' && selected}
						<ConfigPanel
							profileId={id}
							{graph}
							node={selected}
							{registry}
							{template}
							{base}
							issues={issues.filter((i) => i.node_id === selected.id)}
							readonly={busy}
							onfield={(field, value) => (graph = setConfigField(graph, selected.id, field, value))}
							ontoggle={() => toggle(selected.id)}
							onremove={() => remove(selected.id)}
						/>
					{:else if tab === 'marketing'}
						<MarketingEditor
							{graph}
							{issues}
							readonly={busy}
							onmarketing={(mk) => setMeta({ marketing: mk })}
						/>
					{:else if tab === 'versions'}
						<VersionsPanel {agent} {busy} {dirty} onrollback={rollback} onreset={resetDraft} />
					{:else if tab === 'test'}
						<TestChatPanel profileId={id} hasDev={!!agent.dev} />
					{:else}
						<AgentSettingsPanel {graph} {issues} readonly={busy} onmeta={setMeta} />
					{/if}
				</div>
			</aside>
		</div>
	{/if}
</div>

<dialog
	bind:this={publishDialog}
	class="w-full max-w-md rounded-lg border p-0 backdrop:bg-black/40"
	style="border-color: var(--color-border); background: var(--color-surface-2);"
>
	<form class="space-y-3 p-5" onsubmit={publish}>
		<h2 class="text-base font-semibold" style="color: var(--color-text);">
			{m.agent_editor_publish_heading()}
		</h2>
		<p class="text-xs" style="color: var(--color-text-muted);">
			{m.agent_editor_publish_explain()}
		</p>
		<label class="block">
			<span class="field-label">{m.agent_editor_publish_note()}</span>
			<input class="field-input w-full" maxlength="500" bind:value={publishNote} />
		</label>
		<label class="flex items-center gap-2 text-xs" style="color: var(--color-text);">
			<input type="checkbox" bind:checked={assignAll} />
			{m.agent_editor_publish_assign_all()}
		</label>
		{#if listedOnLanding && !assignAll}
			<p class="text-[11px]" style="color: var(--color-warning);">
				{m.agent_editor_publish_landing_warning()}
			</p>
		{/if}
		<div class="flex justify-end gap-2">
			<Button variant="ghost" type="button" onclick={() => publishDialog?.close()}
				>{m.common_cancel()}</Button
			>
			<Button type="submit"><Rocket size={13} />{m.agent_editor_publish()}</Button>
		</div>
	</form>
</dialog>
