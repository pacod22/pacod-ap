<script lang="ts">
	import { untrack } from 'svelte';
	import { Plus, Trash2, Check, X, Play, Send, RefreshCw } from 'lucide-svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import {
		createNewsletterConfigQuery,
		updateNewsletterConfigMutation,
		collectNowMutation,
		sendNowMutation,
		createSourcesQuery,
		createSourceMutation,
		updateSourceMutation,
		deleteSourceMutation,
		createItemsQuery,
		itemDecisionMutation,
		createIssuesQuery,
		createSubscribersQuery,
		updateSubscriberMutation,
		deleteSubscriberMutation,
		type NewsletterConfigValues,
		type NewsletterRunRequest,
		type NewsletterSource,
		type SourceCreateInput
	} from '$lib/features/newsletter/queries';
	import { selectedProfile } from '$lib/stores/selectedProfile.svelte';
	import { m } from '$lib/paraglide/messages';

	let { data } = $props();

	// ----- Config -----
	const configQ = $derived(createNewsletterConfigQuery(selectedProfile.id));
	const updateMut = $derived(updateNewsletterConfigMutation(selectedProfile.id));
	const collectMut = $derived(collectNowMutation(selectedProfile.id));
	const sendMut = $derived(sendNowMutation(selectedProfile.id));

	// ----- Runs / activity (manual "Collect now" / "Send now" + scheduled) -----
	// Newest first (backend orders by id desc). While a run is requested/running
	// the config query polls, so these badges advance live and stop when done.
	const recentRequests = $derived<NewsletterRunRequest[]>(
		$configQ.data?.recent_requests ?? data.initial.recent_requests ?? []
	);
	const isActive = (r: NewsletterRunRequest) => r.status === 'requested' || r.status === 'running';
	const collectActive = $derived(recentRequests.some((r) => r.job === 'collect' && isActive(r)));
	const sendActive = $derived(recentRequests.some((r) => r.job === 'send' && isActive(r)));

	function runStatusStyle(s: string): string {
		if (s === 'done') return 'background: var(--color-brand-soft); color: var(--color-brand);';
		if (s === 'failed') return 'background: var(--color-danger-soft); color: var(--color-danger);';
		if (s === 'running' || s === 'requested')
			return 'background: var(--color-surface-3); color: var(--color-text);';
		return 'background: var(--color-surface-3); color: var(--color-text-muted);';
	}
	function runStatusLabel(s: string): string {
		if (s === 'running') return m.newsletter_runstatus_running();
		if (s === 'done') return m.newsletter_runstatus_done();
		if (s === 'failed') return m.newsletter_runstatus_failed();
		return m.newsletter_runstatus_requested();
	}
	function runDuration(r: NewsletterRunRequest): string {
		if (!r.started_at) return '—';
		const end = r.finished_at ? new Date(r.finished_at) : new Date();
		const ms = end.getTime() - new Date(r.started_at).getTime();
		if (ms < 0) return '—';
		const s = Math.round(ms / 1000);
		return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
	}

	let form = $state<NewsletterConfigValues>({ ...untrack(() => data.initial.effective) });
	let seededFor = $state<string | null | undefined>('__initial__');
	let saveError = $state('');
	let saved = $state(false);

	// Reseed the editable form whenever the selected profile's config loads.
	$effect(() => {
		const eff = $configQ.data?.effective;
		if (eff && seededFor !== selectedProfile.id) {
			form = { ...eff };
			seededFor = selectedProfile.id;
		}
	});

	const weekdays = $derived(m.newsletter_weekdays().split(','));

	function daysSet(csv: string): Set<number> {
		return new Set(
			csv
				.split(',')
				.map((s) => parseInt(s.trim(), 10))
				.filter((n) => !Number.isNaN(n))
		);
	}
	function toggleDay(field: 'collect_days' | 'send_days', day: number) {
		const set = daysSet(form[field]);
		if (set.has(day)) set.delete(day);
		else set.add(day);
		form[field] = [...set].sort((a, b) => a - b).join(',');
	}

	async function saveConfig() {
		saveError = '';
		saved = false;
		try {
			await $updateMut.mutateAsync({
				collect_enabled: form.collect_enabled,
				collect_days: form.collect_days,
				collect_hour: Number(form.collect_hour),
				collect_minute: Number(form.collect_minute),
				send_enabled: form.send_enabled,
				send_days: form.send_days,
				send_hour: Number(form.send_hour),
				send_minute: Number(form.send_minute),
				max_sources: Number(form.max_sources),
				max_articles_per_source: Number(form.max_articles_per_source),
				crawl_concurrency: Number(form.crawl_concurrency),
				max_items_per_issue: Number(form.max_items_per_issue),
				min_relevance: Number(form.min_relevance),
				subject_template: form.subject_template
			});
			saved = true;
		} catch (e: unknown) {
			saveError = e instanceof Error ? e.message : m.common_unexpected_error();
		}
	}

	// ----- Sources -----
	const sourcesQ = $derived(createSourcesQuery(selectedProfile.id));
	const createSourceMut = $derived(createSourceMutation(selectedProfile.id));
	const updateSourceMut = $derived(updateSourceMutation(selectedProfile.id));
	const deleteSourceMut = $derived(deleteSourceMutation(selectedProfile.id));

	let newSource = $state<SourceCreateInput>({
		url: '',
		kind: 'article',
		title: '',
		is_active: true
	});
	async function addSource() {
		if (!newSource.url.trim()) return;
		await $createSourceMut.mutateAsync({ ...newSource });
		newSource = { url: '', kind: 'article', title: '', is_active: true };
	}
	async function toggleSource(s: NewsletterSource) {
		await $updateSourceMut.mutateAsync({ id: s.id, patch: { is_active: !s.is_active } });
	}
	async function removeSource(id: number) {
		if (confirm(m.newsletter_confirm_delete_source())) await $deleteSourceMut.mutateAsync(id);
	}

	// ----- Review queue -----
	let itemStatus = $state('pending');
	const itemsQ = $derived(createItemsQuery(selectedProfile.id, itemStatus));
	const decisionMut = $derived(itemDecisionMutation(selectedProfile.id));
	async function decide(id: number, decision: 'approve' | 'reject') {
		await $decisionMut.mutateAsync({ id, decision });
	}
	const pct = (v: number) => `${Math.round(v * 100)}%`;

	// ----- Issues -----
	const issuesQ = $derived(createIssuesQuery(selectedProfile.id));

	// ----- Public sign-up target (global pointer, surfaced per-profile) -----
	const isPublicTarget = $derived(
		$configQ.data?.public_subscribe ?? data.initial.public_subscribe ?? false
	);
	async function togglePublic(v: boolean) {
		saveError = '';
		try {
			await $updateMut.mutateAsync({ public_subscribe: v });
		} catch (e: unknown) {
			saveError = e instanceof Error ? e.message : m.common_unexpected_error();
		}
	}

	// ----- Subscribers -----
	let subStatus = $state('');
	const subscribersQ = $derived(createSubscribersQuery(selectedProfile.id, subStatus || undefined));
	const updateSubMut = $derived(updateSubscriberMutation(selectedProfile.id));
	const deleteSubMut = $derived(deleteSubscriberMutation(selectedProfile.id));
	async function setSubStatus(id: number, status: 'subscribed' | 'unsubscribed') {
		await $updateSubMut.mutateAsync({ id, status });
	}
	async function removeSubscriber(id: number) {
		if (confirm(m.newsletter_confirm_delete_subscriber())) await $deleteSubMut.mutateAsync(id);
	}
</script>

<svelte:head><title>{m.newsletter_title()}</title></svelte:head>

<div class="space-y-8">
	<div class="flex items-start justify-between gap-4">
		<div>
			<h1 class="text-xl font-bold">{m.newsletter_title()}</h1>
			<p class="text-sm text-[var(--color-text-muted)]">{m.newsletter_subtitle()}</p>
		</div>
		<div class="flex gap-2">
			<Button
				variant="secondary"
				size="sm"
				onclick={() => $collectMut.mutate()}
				disabled={$collectMut.isPending || collectActive}
			>
				{#if collectActive}<RefreshCw size={14} class="animate-spin" /> {m.newsletter_collecting()}
				{:else}<Play size={14} /> {m.newsletter_collect_now()}{/if}
			</Button>
			<Button
				variant="secondary"
				size="sm"
				onclick={() => $sendMut.mutate()}
				disabled={$sendMut.isPending || sendActive}
			>
				{#if sendActive}<RefreshCw size={14} class="animate-spin" /> {m.newsletter_sending()}
				{:else}<Send size={14} /> {m.newsletter_send_now()}{/if}
			</Button>
		</div>
	</div>

	<!-- Activity (manual "Collect now" / "Send now" + scheduled runs) -->
	<section class="rounded-xl border border-[var(--color-border)] p-5">
		<div class="mb-4 flex items-center gap-2">
			<h2 class="text-base font-semibold">{m.newsletter_section_activity()}</h2>
			{#if $configQ.isFetching}<RefreshCw
					size={13}
					class="animate-spin text-[var(--color-text-muted)]"
				/>{/if}
		</div>
		{#if recentRequests.length === 0}
			<p class="text-sm text-[var(--color-text-muted)]">{m.newsletter_no_runs()}</p>
		{:else}
			<table class="data-table data-table-stack w-full">
				<thead>
					<tr>
						<th>{m.newsletter_run_job()}</th><th>{m.newsletter_run_trigger()}</th>
						<th>{m.newsletter_issue_status()}</th><th>{m.newsletter_run_requested()}</th>
						<th>{m.newsletter_run_finished()}</th><th>{m.newsletter_run_duration()}</th>
					</tr>
				</thead>
				<tbody>
					{#each recentRequests as r (r.id)}
						<tr>
							<td data-label={m.newsletter_run_job()}
								>{r.job === 'send' ? m.newsletter_job_send() : m.newsletter_job_collect()}</td
							>
							<td data-label={m.newsletter_run_trigger()}
								>{r.trigger === 'scheduled'
									? m.newsletter_trigger_scheduled()
									: m.newsletter_trigger_manual()}</td
							>
							<td data-label={m.newsletter_issue_status()}>
								<span
									class="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-xs font-medium"
									style={runStatusStyle(r.status)}
								>
									{#if r.status === 'running'}<RefreshCw size={11} class="animate-spin" />{/if}
									{runStatusLabel(r.status)}
								</span>
								{#if r.error}<div
										class="mt-1 max-w-[260px] truncate text-xs"
										style="color: var(--color-danger);"
										title={r.error}
									>
										{r.error}
									</div>{/if}
							</td>
							<td data-label={m.newsletter_run_requested()}
								>{new Date(r.requested_at).toLocaleString()}</td
							>
							<td data-label={m.newsletter_run_finished()}
								>{r.finished_at ? new Date(r.finished_at).toLocaleString() : '—'}</td
							>
							<td data-label={m.newsletter_run_duration()}>{runDuration(r)}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		{/if}
	</section>

	<!-- Config -->
	<section class="rounded-xl border border-[var(--color-border)] p-5">
		<h2 class="mb-4 text-base font-semibold">{m.newsletter_section_schedule()}</h2>
		<div class="grid gap-6 md:grid-cols-2">
			{#each [{ k: 'collect', enabled: 'collect_enabled', days: 'collect_days', hour: 'collect_hour', minute: 'collect_minute', label: m.newsletter_collect() }, { k: 'send', enabled: 'send_enabled', days: 'send_days', hour: 'send_hour', minute: 'send_minute', label: m.newsletter_send() }] as job}
				<div class="rounded-lg border border-[var(--color-border)] p-4">
					<label class="flex items-center gap-2 font-medium">
						<input
							type="checkbox"
							bind:checked={form[job.enabled as keyof NewsletterConfigValues] as boolean}
						/>
						{job.label}
					</label>
					<div class="mt-3">
						<span class="field-label">{m.newsletter_days()}</span>
						<div class="mt-1 flex flex-wrap gap-1">
							{#each weekdays as wd, i}
								<button
									type="button"
									onclick={() => toggleDay(job.days as 'collect_days' | 'send_days', i)}
									class="touch-target rounded-md border px-2 py-1 text-xs"
									class:border-[var(--color-brand)]={daysSet(
										form[job.days as 'collect_days' | 'send_days']
									).has(i)}
									style={daysSet(form[job.days as 'collect_days' | 'send_days']).has(i)
										? 'background: var(--color-brand-soft); color: var(--color-brand);'
										: ''}>{wd}</button
								>
							{/each}
						</div>
					</div>
					<div class="mt-3 flex items-end gap-2">
						<div>
							<span class="field-label">{m.newsletter_time()} (UTC)</span>
							<div class="flex items-center gap-1">
								<input
									type="number"
									min="0"
									max="23"
									class="field-input w-16"
									bind:value={form[job.hour as keyof NewsletterConfigValues]}
								/>
								<span>:</span>
								<input
									type="number"
									min="0"
									max="59"
									class="field-input w-16"
									bind:value={form[job.minute as keyof NewsletterConfigValues]}
								/>
							</div>
						</div>
					</div>
				</div>
			{/each}
		</div>

		<h2 class="mt-6 mb-3 text-base font-semibold">{m.newsletter_section_bounds()}</h2>
		<div class="grid grid-cols-2 gap-4 md:grid-cols-4">
			<div>
				<span class="field-label">{m.newsletter_max_sources()}</span>
				<input type="number" min="1" class="field-input" bind:value={form.max_sources} />
			</div>
			<div>
				<span class="field-label">{m.newsletter_max_articles()}</span>
				<input
					type="number"
					min="1"
					class="field-input"
					bind:value={form.max_articles_per_source}
				/>
			</div>
			<div>
				<span class="field-label">{m.newsletter_concurrency()}</span>
				<input type="number" min="1" class="field-input" bind:value={form.crawl_concurrency} />
			</div>
			<div>
				<span class="field-label">{m.newsletter_max_items()}</span>
				<input type="number" min="1" class="field-input" bind:value={form.max_items_per_issue} />
			</div>
		</div>
		<div class="mt-4">
			<span class="field-label">{m.newsletter_min_relevance()}</span>
			<input
				type="number"
				min="0"
				max="1"
				step="0.05"
				class="field-input"
				bind:value={form.min_relevance}
			/>
			<p class="mt-1 text-xs text-[var(--color-text-muted)]">{m.newsletter_min_relevance_hint()}</p>
		</div>
		<div class="mt-4">
			<span class="field-label">{m.newsletter_subject()}</span>
			<input type="text" class="field-input" bind:value={form.subject_template} />
			<p class="mt-1 text-xs text-[var(--color-text-muted)]">{m.newsletter_subject_hint()}</p>
		</div>
		<p class="mt-3 text-xs text-[var(--color-text-muted)]">{m.newsletter_model_hint()}</p>

		<div class="mt-5 rounded-lg border border-[var(--color-border)] p-4">
			<label class="flex items-center gap-2 font-medium">
				<input
					type="checkbox"
					checked={isPublicTarget}
					onchange={(e) => togglePublic(e.currentTarget.checked)}
				/>
				{m.newsletter_public_toggle()}
			</label>
			<p class="mt-1 text-xs text-[var(--color-text-muted)]">{m.newsletter_public_hint()}</p>
		</div>

		<div class="mt-4 flex items-center gap-3">
			<Button size="sm" onclick={saveConfig} disabled={$updateMut.isPending}
				>{m.newsletter_save()}</Button
			>
			{#if saved}<span class="text-sm text-green-600">{m.newsletter_saved()}</span>{/if}
			{#if saveError}<span class="text-sm text-red-600">{saveError}</span>{/if}
		</div>
	</section>

	<!-- Sources -->
	<section class="rounded-xl border border-[var(--color-border)] p-5">
		<h2 class="mb-4 text-base font-semibold">{m.newsletter_section_sources()}</h2>
		<div class="mb-4 flex flex-wrap items-end gap-2">
			<div class="min-w-[240px] flex-1">
				<span class="field-label">{m.newsletter_url()}</span>
				<input type="text" class="field-input" placeholder="https://…" bind:value={newSource.url} />
			</div>
			<div>
				<span class="field-label">{m.newsletter_kind()}</span>
				<select class="field-input" bind:value={newSource.kind}>
					<option value="article">{m.newsletter_kind_article()}</option>
					<option value="index">{m.newsletter_kind_index()}</option>
					<option value="feed">{m.newsletter_kind_feed()}</option>
				</select>
			</div>
			<div>
				<span class="field-label">{m.newsletter_source_title()}</span>
				<input type="text" class="field-input" bind:value={newSource.title} />
			</div>
			<Button size="sm" onclick={addSource} disabled={$createSourceMut.isPending}>
				<Plus size={14} />
				{m.newsletter_add_source()}
			</Button>
		</div>
		{#if ($sourcesQ.data ?? []).length === 0}
			<p class="text-sm text-[var(--color-text-muted)]">{m.newsletter_no_sources()}</p>
		{:else}
			<table class="data-table data-table-stack w-full">
				<thead>
					<tr>
						<th>{m.newsletter_url()}</th><th>{m.newsletter_kind()}</th>
						<th>{m.newsletter_source_title()}</th><th>{m.newsletter_active()}</th><th></th>
					</tr>
				</thead>
				<tbody>
					{#each $sourcesQ.data ?? [] as s (s.id)}
						<tr>
							<td data-label={m.newsletter_url()} class="max-w-[320px] truncate"
								><a
									href={s.url}
									target="_blank"
									rel="noreferrer"
									class="text-[var(--color-brand)] hover:underline">{s.url}</a
								></td
							>
							<td data-label={m.newsletter_kind()}>{s.kind}</td>
							<td data-label={m.newsletter_source_title()}>{s.title ?? '—'}</td>
							<td data-label={m.newsletter_active()}>
								<input type="checkbox" checked={s.is_active} onchange={() => toggleSource(s)} />
							</td>
							<td>
								<Button variant="danger-ghost" size="icon" onclick={() => removeSource(s.id)}
									><Trash2 size={14} /></Button
								>
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		{/if}
	</section>

	<!-- Review queue -->
	<section class="rounded-xl border border-[var(--color-border)] p-5">
		<div class="mb-4 flex items-center justify-between gap-3">
			<h2 class="text-base font-semibold">{m.newsletter_section_review()}</h2>
			<select class="field-input w-40" bind:value={itemStatus}>
				<option value="pending">{m.newsletter_status_pending()}</option>
				<option value="approved">{m.newsletter_status_approved()}</option>
				<option value="rejected">{m.newsletter_status_rejected()}</option>
				<option value="sent">{m.newsletter_status_sent()}</option>
			</select>
		</div>
		{#if ($itemsQ.data ?? []).length === 0}
			<p class="text-sm text-[var(--color-text-muted)]">{m.newsletter_no_items()}</p>
		{:else}
			<div class="space-y-3">
				{#each $itemsQ.data ?? [] as item (item.id)}
					<div class="rounded-lg border border-[var(--color-border)] p-4">
						<div class="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between sm:gap-4">
							<div class="min-w-0">
								<div class="font-semibold break-words">
									{#if item.article_url}<a
											href={item.article_url}
											target="_blank"
											rel="noreferrer"
											class="hover:underline">{item.title}</a
										>{:else}{item.title}{/if}
								</div>
								<p class="mt-1 text-sm break-words text-[var(--color-text-muted)]">
									{item.summary}
								</p>
								<div class="mt-2 flex flex-wrap gap-3 text-xs text-[var(--color-text-muted)]">
									<span>{m.newsletter_relevance()}: {pct(item.relevance)}</span>
									<span>{m.newsletter_importance()}: {pct(item.importance)}</span>
									<span>{m.newsletter_urgency()}: {pct(item.urgency)}</span>
									{#if item.source_label}<span>· {item.source_label}</span>{/if}
								</div>
							</div>
							{#if item.status === 'pending'}
								<div class="flex flex-wrap gap-2 sm:shrink-0">
									<Button variant="secondary" size="sm" onclick={() => decide(item.id, 'approve')}
										><Check size={14} /> {m.newsletter_approve()}</Button
									>
									<Button variant="danger-ghost" size="sm" onclick={() => decide(item.id, 'reject')}
										><X size={14} /> {m.newsletter_reject()}</Button
									>
								</div>
							{:else}
								<span class="text-xs sm:shrink-0" style="color: var(--color-text-muted);"
									>{item.status}</span
								>
							{/if}
						</div>
					</div>
				{/each}
			</div>
		{/if}
	</section>

	<!-- Issues -->
	<section class="rounded-xl border border-[var(--color-border)] p-5">
		<h2 class="mb-4 text-base font-semibold">{m.newsletter_section_issues()}</h2>
		{#if ($issuesQ.data ?? []).length === 0}
			<p class="text-sm text-[var(--color-text-muted)]">{m.newsletter_no_issues()}</p>
		{:else}
			<table class="data-table data-table-stack w-full">
				<thead>
					<tr>
						<th>{m.newsletter_issue_subject()}</th><th>{m.newsletter_issue_items()}</th>
						<th>{m.newsletter_issue_recipients()}</th><th>{m.newsletter_issue_status()}</th>
						<th>{m.newsletter_issue_sent_at()}</th>
					</tr>
				</thead>
				<tbody>
					{#each $issuesQ.data ?? [] as issue (issue.id)}
						<tr>
							<td data-label={m.newsletter_issue_subject()}>{issue.subject}</td>
							<td data-label={m.newsletter_issue_items()}>{issue.item_count}</td>
							<td data-label={m.newsletter_issue_recipients()}>{issue.recipient_count}</td>
							<td data-label={m.newsletter_issue_status()}>{issue.status}</td>
							<td data-label={m.newsletter_issue_sent_at()}
								>{issue.sent_at ? new Date(issue.sent_at).toLocaleString() : '—'}</td
							>
						</tr>
					{/each}
				</tbody>
			</table>
		{/if}
	</section>

	<!-- Subscribers -->
	<section class="rounded-xl border border-[var(--color-border)] p-5">
		<div class="mb-4 flex items-center justify-between gap-3">
			<h2 class="text-base font-semibold">{m.newsletter_section_subscribers()}</h2>
			<select class="field-input w-44" bind:value={subStatus}>
				<option value="">{m.newsletter_sub_all()}</option>
				<option value="subscribed">{m.newsletter_substatus_subscribed()}</option>
				<option value="pending">{m.newsletter_substatus_pending()}</option>
				<option value="unsubscribed">{m.newsletter_substatus_unsubscribed()}</option>
			</select>
		</div>
		{#if ($subscribersQ.data ?? []).length === 0}
			<p class="text-sm text-[var(--color-text-muted)]">{m.newsletter_no_subscribers()}</p>
		{:else}
			<table class="data-table data-table-stack w-full">
				<thead>
					<tr>
						<th>{m.newsletter_sub_email()}</th><th>{m.newsletter_sub_kind()}</th>
						<th>{m.newsletter_sub_status()}</th><th>{m.newsletter_sub_name()}</th>
						<th>{m.newsletter_sub_created()}</th><th></th>
					</tr>
				</thead>
				<tbody>
					{#each $subscribersQ.data ?? [] as sub (sub.id)}
						<tr>
							<td data-label={m.newsletter_sub_email()} class="max-w-[280px] truncate"
								>{sub.email ?? '—'}</td
							>
							<td data-label={m.newsletter_sub_kind()}>
								{sub.kind === 'account'
									? m.newsletter_kind_account()
									: m.newsletter_kind_email_only()}
							</td>
							<td data-label={m.newsletter_sub_status()}>
								{#if sub.status === 'subscribed'}{m.newsletter_substatus_subscribed()}
								{:else if sub.status === 'pending'}{m.newsletter_substatus_pending()}
								{:else}{m.newsletter_substatus_unsubscribed()}{/if}
							</td>
							<td data-label={m.newsletter_sub_name()}>{sub.display_name ?? '—'}</td>
							<td data-label={m.newsletter_sub_created()}
								>{new Date(sub.created_at).toLocaleDateString()}</td
							>
							<td>
								<div class="flex justify-end gap-1">
									{#if sub.status === 'unsubscribed' || sub.status === 'pending'}
										<Button
											variant="secondary"
											size="sm"
											onclick={() => setSubStatus(sub.id, 'subscribed')}
											disabled={$updateSubMut.isPending}
										>
											<Check size={14} />
											{m.newsletter_sub_resubscribe()}
										</Button>
									{:else}
										<Button
											variant="secondary"
											size="sm"
											onclick={() => setSubStatus(sub.id, 'unsubscribed')}
											disabled={$updateSubMut.isPending}
										>
											<X size={14} />
											{m.newsletter_sub_unsubscribe()}
										</Button>
									{/if}
									<Button
										variant="danger-ghost"
										size="icon"
										onclick={() => removeSubscriber(sub.id)}><Trash2 size={14} /></Button
									>
								</div>
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		{/if}
	</section>
</div>
