<script lang="ts">
	/**
	 * One user's IP addresses (use count, first/last use) and activity log
	 * (logins, chat turns, …) — `user_ip_addresses` / `user_activity`
	 * on the backend. Admin-only, like the page it sits on. Not filtered by the
	 * page's range/site/profile filters: the log has its own retention window.
	 */
	import { m } from '$lib/paraglide/messages';
	import { ChevronLeft, ChevronRight } from 'lucide-svelte';
	import {
		ACTIVITY_ACTIONS,
		createUserActivityQuery,
		createUserIpsQuery,
		type ActivityAction,
		type UserActivityRow
	} from './queries';
	import { fmtDateTime, num, siteLabel } from './format';

	let { userId, profileLabel }: { userId: string; profileLabel: (id: string) => string } = $props();

	const PAGE_SIZE = 50;
	let action = $state<ActivityAction | ''>('');
	let offset = $state(0);

	const ips = $derived(createUserIpsQuery(userId));
	const activity = $derived(
		createUserActivityQuery(userId, {
			action: action || undefined,
			limit: PAGE_SIZE,
			offset
		})
	);
	const rows = $derived($activity.data?.items ?? []);
	const total = $derived($activity.data?.total ?? 0);
	const hasPrev = $derived(offset > 0);
	const hasNext = $derived(offset + rows.length < total);

	const ACTION_LABELS: Record<ActivityAction, () => string> = {
		login: m.analytics_activity_login,
		chat_turn: m.analytics_activity_chat_turn,
		chat_deleted: m.analytics_activity_chat_deleted
	};
	function actionLabel(a: string): string {
		return ACTION_LABELS[a as ActivityAction]?.() ?? a;
	}

	/** Chat turns and deletions link into Conversations. */
	function chatHref(r: UserActivityRow): string | null {
		if (!r.session_id || (r.action !== 'chat_turn' && r.action !== 'chat_deleted')) return null;
		const p = new URLSearchParams({ user_id: userId, session_id: r.session_id });
		return `/conversations?${p}`;
	}

	function detailText(r: UserActivityRow): string {
		const d = r.detail ?? {};
		const parts: string[] = [];
		for (const [k, v] of Object.entries(d)) {
			parts.push(`${k}: ${typeof v === 'string' ? v : JSON.stringify(v)}`);
		}
		return parts.join(' · ') || '—';
	}

	function changeAction() {
		offset = 0;
	}
</script>

<!-- IP addresses -->
<section class="overflow-hidden rounded-xl border" style="border-color: var(--color-border);">
	<h2 class="px-4 pt-3 pb-2 text-base font-semibold">{m.analytics_ips_title()}</h2>
	{#if $ips.isError}
		<p class="p-4 text-sm" style="color: var(--color-danger);">{m.analytics_load_failed()}</p>
	{:else if !$ips.data || $ips.data.items.length === 0}
		<p class="p-4 text-sm text-[var(--color-text-muted)]">
			{$ips.isPending ? m.common_loading() : m.analytics_ips_empty()}
		</p>
	{:else}
		<div class="overflow-x-auto">
			<table class="data-table data-table-stack">
				<thead>
					<tr>
						<th>{m.analytics_col_ip()}</th>
						<th style="text-align: right">{m.analytics_col_uses()}</th>
						<th>{m.analytics_col_first_seen()}</th>
						<th>{m.analytics_col_last_used()}</th>
					</tr>
				</thead>
				<tbody>
					{#each $ips.data.items as r (r.ip)}
						<tr>
							<td data-label={m.analytics_col_ip()} class="font-mono text-xs">{r.ip}</td>
							<td data-label={m.analytics_col_uses()} class="text-right tabular-nums"
								>{num(r.use_count)}</td
							>
							<td data-label={m.analytics_col_first_seen()} class="text-xs tabular-nums"
								>{fmtDateTime(r.first_seen_at)}</td
							>
							<td data-label={m.analytics_col_last_used()} class="text-xs tabular-nums"
								>{fmtDateTime(r.last_used_at)}</td
							>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{/if}
</section>

<!-- Activity log -->
<section class="overflow-hidden rounded-xl border" style="border-color: var(--color-border);">
	<div class="flex flex-wrap items-end justify-between gap-2 px-4 pt-3 pb-2">
		<div>
			<h2 class="text-base font-semibold">{m.analytics_activity_title()}</h2>
			{#if $activity.data}
				<p class="text-xs text-[var(--color-text-muted)]">
					{$activity.data.retention_months > 0
						? m.analytics_activity_retention({ months: $activity.data.retention_months })
						: m.analytics_activity_retention_forever()}
				</p>
			{/if}
		</div>
		<label class="text-sm">
			<span class="sr-only">{m.analytics_col_action()}</span>
			<select class="field-input h-9" bind:value={action} onchange={changeAction}>
				<option value="">{m.analytics_activity_all_actions()}</option>
				{#each ACTIVITY_ACTIONS as a (a)}
					<option value={a}>{actionLabel(a)}</option>
				{/each}
			</select>
		</label>
	</div>
	{#if $activity.isError}
		<p class="p-4 text-sm" style="color: var(--color-danger);">{m.analytics_load_failed()}</p>
	{:else if rows.length === 0}
		<p class="p-4 text-sm text-[var(--color-text-muted)]">
			{$activity.isPending ? m.common_loading() : m.analytics_activity_empty()}
		</p>
	{:else}
		<div class="overflow-x-auto">
			<table class="data-table data-table-stack">
				<thead>
					<tr>
						<th>{m.analytics_col_time()}</th>
						<th>{m.analytics_col_action()}</th>
						<th>{m.analytics_col_ip()}</th>
						<th>{m.analytics_col_site()}</th>
						<th>{m.analytics_col_profile()}</th>
						<th>{m.analytics_col_chat()}</th>
						<th>{m.analytics_col_detail()}</th>
					</tr>
				</thead>
				<tbody>
					{#each rows as r (r.id)}
						{@const href = chatHref(r)}
						<tr>
							<td
								data-label={m.analytics_col_time()}
								class="text-xs whitespace-nowrap text-[var(--color-text-muted)] tabular-nums"
								>{fmtDateTime(r.occurred_at)}</td
							>
							<td data-label={m.analytics_col_action()}>{actionLabel(r.action)}</td>
							<td data-label={m.analytics_col_ip()} class="font-mono text-xs">{r.ip ?? '—'}</td>
							<td data-label={m.analytics_col_site()}>{r.site ? siteLabel(r.site) : '—'}</td>
							<td data-label={m.analytics_col_profile()}
								>{r.profile_id ? (r.profile_name ?? profileLabel(r.profile_id)) : '—'}</td
							>
							<td
								data-label={m.analytics_col_chat()}
								class="max-w-[220px] truncate font-mono text-xs"
							>
								{#if href}
									<a {href} class="underline" title={r.session_id ?? ''}>{r.session_id}</a>
								{:else}
									—
								{/if}
							</td>
							<td
								data-label={m.analytics_col_detail()}
								class="stack-block max-w-[320px] truncate text-xs"
								title={detailText(r)}>{detailText(r)}</td
							>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
		<div class="flex items-center justify-between px-4 py-2 text-sm text-[var(--color-text-muted)]">
			<span>
				{m.common_showing_range({ from: offset + 1, to: offset + rows.length })} / {num(total)}
			</span>
			<div class="flex items-center gap-2">
				<button
					onclick={() => (offset = Math.max(0, offset - PAGE_SIZE))}
					disabled={!hasPrev}
					class="flex items-center gap-1 rounded-lg px-3 py-1.5 text-sm transition-colors hover:bg-[var(--color-surface-2)] disabled:pointer-events-none disabled:opacity-40"
				>
					<ChevronLeft size={14} />
					{m.common_prev()}
				</button>
				<button
					onclick={() => (offset += PAGE_SIZE)}
					disabled={!hasNext}
					class="flex items-center gap-1 rounded-lg px-3 py-1.5 text-sm transition-colors hover:bg-[var(--color-surface-2)] disabled:pointer-events-none disabled:opacity-40"
				>
					{m.common_next()}
					<ChevronRight size={14} />
				</button>
			</div>
		</div>
	{/if}
</section>
