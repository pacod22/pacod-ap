<script lang="ts">
	import { Mail, Shield, BadgeCheck, Hash, KeyRound, ExternalLink } from 'lucide-svelte';
	import { enhance } from '$app/forms';
	import DailyUsageCard from '$lib/components/dashboard/DailyUsageCard.svelte';
	import ActivityCard from '$lib/components/dashboard/ActivityCard.svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import { userLabel, userInitial, hasRole } from '$lib/types/auth';
	import { m } from '$lib/paraglide/messages';

	let { data, form } = $props();
	const user = $derived(data.user);
	const newsletters = $derived(data.newsletters ?? []);

	const roleLabel = $derived(
		hasRole(user, 'admin') ? m.account_role_admin() : m.account_role_member()
	);
</script>

<svelte:head><title>{m.account_title()}</title></svelte:head>

<div class="mx-auto max-w-3xl space-y-8">
	<!-- Header -->
	<div>
		<h1 class="text-2xl font-semibold tracking-tight">{m.account_settings_heading()}</h1>
		<p class="mt-1 text-sm text-[var(--color-text-muted)]">
			{m.account_settings_subtitle()}
		</p>
	</div>

	<!-- Profile -->
	<section
		class="rounded-xl border p-6"
		style="background: var(--color-surface-2); border-color: var(--color-border);"
	>
		<div class="flex items-center gap-4">
			<div
				class="grid h-14 w-14 shrink-0 place-items-center rounded-full text-xl font-semibold"
				style="background: var(--color-brand-soft); color: var(--color-brand);"
				aria-hidden="true"
			>
				{userInitial(user)}
			</div>
			<div class="min-w-0">
				<div class="truncate text-lg font-semibold">{userLabel(user)}</div>
				<div class="text-sm text-[var(--color-text-muted)]">{roleLabel}</div>
			</div>
		</div>

		<dl class="mt-6 grid gap-4 sm:grid-cols-2">
			<div class="flex items-start gap-2.5">
				<Mail size={16} class="mt-0.5 shrink-0 text-[var(--color-text-muted)]" />
				<div class="min-w-0">
					<dt class="field-label">{m.account_email()}</dt>
					<dd class="truncate text-sm">{user.email ?? '—'}</dd>
				</div>
			</div>
			<div class="flex items-start gap-2.5">
				<Hash size={16} class="mt-0.5 shrink-0 text-[var(--color-text-muted)]" />
				<div class="min-w-0">
					<dt class="field-label">{m.account_user_id()}</dt>
					<dd class="truncate font-mono text-sm">{user.user_id}</dd>
				</div>
			</div>
			<div class="flex items-start gap-2.5">
				<Shield size={16} class="mt-0.5 shrink-0 text-[var(--color-text-muted)]" />
				<div class="min-w-0">
					<dt class="field-label">{m.account_roles()}</dt>
					<dd class="text-sm">{user.roles.length ? user.roles.join(', ') : '—'}</dd>
				</div>
			</div>
			<div class="flex items-start gap-2.5">
				<BadgeCheck size={16} class="mt-0.5 shrink-0 text-[var(--color-text-muted)]" />
				<div class="min-w-0">
					<dt class="field-label">{m.account_status()}</dt>
					<dd class="text-sm">
						{user.is_active ? m.account_active() : m.account_inactive()}
					</dd>
				</div>
			</div>
		</dl>
	</section>

	<!-- Newsletter subscriptions -->
	{#if newsletters.length}
		<section>
			<h2 class="mb-3 text-sm font-semibold tracking-wide text-[var(--color-text-muted)] uppercase">
				{m.account_newsletter()}
			</h2>
			<p class="mb-3 text-sm text-[var(--color-text-muted)]">{m.account_newsletter_subtitle()}</p>
			{#if form?.error}
				<p class="mb-3 text-sm text-[var(--color-danger)]">{form.error}</p>
			{/if}
			<div
				class="divide-y divide-[var(--color-border)] rounded-xl border border-[var(--color-border)]"
			>
				{#each newsletters as n (n.profile_id)}
					<div class="flex items-center justify-between gap-4 p-4">
						<div class="font-medium">{n.name}</div>
						<form method="post" action="?/toggleNewsletter" use:enhance>
							<input type="hidden" name="profile_id" value={n.profile_id} />
							<input type="hidden" name="subscribe" value={(!n.subscribed).toString()} />
							<Button type="submit" variant={n.subscribed ? 'danger-ghost' : 'primary'} size="sm">
								{n.subscribed
									? m.account_newsletter_unsubscribe()
									: m.account_newsletter_subscribe()}
							</Button>
						</form>
					</div>
				{/each}
			</div>
		</section>
	{/if}

	<!-- Token usage -->
	<section>
		<h2 class="mb-3 text-sm font-semibold tracking-wide text-[var(--color-text-muted)] uppercase">
			{m.account_token_usage()}
		</h2>
		<DailyUsageCard />
	</section>

	<!-- Sign-in & security: password, devices and Google live at pacod-id -->
	<section>
		<h2 class="mb-3 text-sm font-semibold tracking-wide text-[var(--color-text-muted)] uppercase">
			{m.account_security()}
		</h2>
		<div
			class="flex flex-wrap items-center justify-between gap-4 rounded-xl border p-4"
			style="background: var(--color-surface-2); border-color: var(--color-border);"
		>
			<div class="flex min-w-0 items-start gap-3">
				<KeyRound size={18} class="mt-0.5 shrink-0 text-[var(--color-text-muted)]" />
				<p class="text-sm text-[var(--color-text-muted)]">{m.account_security_subtitle()}</p>
			</div>
			<a
				href={data.accountUrl}
				target="_blank"
				rel="noopener noreferrer"
				class="inline-flex shrink-0 items-center gap-1.5 rounded-lg border px-3 py-1.5 text-sm font-medium transition-colors hover:bg-[var(--color-surface)]"
				style="border-color: var(--color-border);"
			>
				{m.account_security_manage()}
				<ExternalLink size={14} />
			</a>
		</div>
	</section>

	<!-- Activity -->
	<section>
		<h2 class="mb-3 text-sm font-semibold tracking-wide text-[var(--color-text-muted)] uppercase">
			{m.account_activity()}
		</h2>
		<ActivityCard />
	</section>
</div>
