<script lang="ts">
	/** Version history (immutable snapshots) + deployment log, with rollback and
	 *  "load into draft". */
	import { untrack } from 'svelte';
	import { History, RotateCcw, FileDown } from 'lucide-svelte';
	import { m } from '$lib/paraglide/messages';
	import Button from '$lib/components/ui/Button.svelte';
	import {
		deploymentsQuery,
		versionsQuery,
		type AgentDetail
	} from '$lib/features/agent-editor/queries';

	interface Props {
		agent: AgentDetail;
		busy: boolean;
		dirty: boolean;
		onrollback: (versionId: number) => void;
		onreset: (versionId: number) => void;
	}

	let { agent, busy, dirty, onrollback, onreset }: Props = $props();

	// The agent id never changes for the life of the panel.
	const agentId = untrack(() => agent.profile_id);
	const versions = versionsQuery(agentId);
	const deployments = deploymentsQuery(agentId);

	function when(iso: string | null): string {
		return iso ? new Date(iso).toLocaleString() : '—';
	}
	const ACTION_LABELS: Record<string, () => string> = {
		deploy_dev: m.agent_editor_action_deploy_dev,
		undeploy_dev: m.agent_editor_action_undeploy_dev,
		publish: m.agent_editor_action_publish,
		rollback: m.agent_editor_action_rollback,
		unpublish: m.agent_editor_action_unpublish,
		republish: m.agent_editor_action_republish,
		archive: m.agent_editor_action_archive,
		adopt: m.agent_editor_action_adopt
	};
</script>

<div class="space-y-4">
	<div>
		<h2
			class="mb-2 flex items-center gap-1.5 text-sm font-semibold"
			style="color: var(--color-text);"
		>
			<History size={14} />{m.agent_editor_versions_title()}
		</h2>
		{#if $versions.isLoading}
			<p class="text-xs" style="color: var(--color-text-faint);">{m.common_loading()}</p>
		{:else}
			<div class="space-y-1.5">
				{#each $versions.data ?? [] as v (v.id)}
					{@const isProd = agent.published?.id === v.id}
					{@const isDev = agent.dev?.id === v.id}
					<div class="rounded-md border p-2 text-xs" style="border-color: var(--color-border);">
						<div class="flex items-center gap-1.5">
							<span class="font-semibold" style="color: var(--color-text);">v{v.version_no}</span>
							<span style="color: var(--color-text-faint);">
								{v.kind === 'release' ? m.agent_editor_kind_release() : m.agent_editor_kind_dev()}
							</span>
							{#if isProd}
								<span
									class="rounded-full px-1.5 text-[10px] font-semibold"
									style="background: var(--color-success-soft); color: var(--color-success);"
									>PROD</span
								>
							{/if}
							{#if isDev}
								<span
									class="rounded-full px-1.5 text-[10px] font-semibold"
									style="background: var(--color-warning-soft); color: var(--color-warning);"
									>DEV</span
								>
							{/if}
						</div>
						<div class="mt-0.5" style="color: var(--color-text-muted);">
							{when(v.created_at)} · {v.created_by ?? '—'}
						</div>
						{#if v.note}
							<div class="mt-0.5 italic" style="color: var(--color-text-muted);">{v.note}</div>
						{/if}
						<div class="mt-1.5 flex gap-1">
							{#if v.kind === 'release' && !isProd}
								<Button
									variant="secondary"
									size="xs"
									disabled={busy}
									title={m.agent_editor_rollback_title()}
									onclick={() => onrollback(v.id)}
								>
									<RotateCcw size={11} />{m.agent_editor_rollback()}
								</Button>
							{/if}
							<Button
								variant="ghost"
								size="xs"
								disabled={busy}
								title={dirty
									? m.agent_editor_reset_draft_dirty()
									: m.agent_editor_reset_draft_title()}
								onclick={() => onreset(v.id)}
							>
								<FileDown size={11} />{m.agent_editor_reset_draft()}
							</Button>
						</div>
					</div>
				{:else}
					<p class="text-xs" style="color: var(--color-text-faint);">
						{m.agent_editor_versions_empty()}
					</p>
				{/each}
			</div>
		{/if}
	</div>

	<div>
		<h3 class="mb-1.5 text-xs font-semibold" style="color: var(--color-text-muted);">
			{m.agent_editor_deployments_title()}
		</h3>
		<ul class="space-y-1 text-[11px]" style="color: var(--color-text-muted);">
			{#each $deployments.data ?? [] as d (d.id)}
				<li>
					<span style="color: var(--color-text);">{ACTION_LABELS[d.action]?.() ?? d.action}</span>
					· {when(d.at)} · {d.actor ?? '—'}
				</li>
			{:else}
				<li>{m.agent_editor_deployments_empty()}</li>
			{/each}
		</ul>
	</div>
</div>
