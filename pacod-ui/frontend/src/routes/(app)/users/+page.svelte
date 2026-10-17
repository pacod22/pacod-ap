<script lang="ts">
	import {
		Plus,
		Trash2,
		Pencil,
		X,
		Check,
		RefreshCw,
		KeyRound,
		Search,
		ArrowUp,
		ArrowDown,
		ArrowUpDown,
		ChartColumn
	} from 'lucide-svelte';
	import { useQueryClient } from '@tanstack/svelte-query';
	import Button from '$lib/components/ui/Button.svelte';
	import {
		createUserListQuery,
		createUserMutation,
		updateUserMutation,
		deleteUserMutation,
		sendPasswordResetMutation,
		userKeys,
		grantedRoles,
		sameGrantedRoles,
		userErrorMessage,
		type User,
		type UserPatch
	} from '$lib/features/users/queries';
	import { resetUserQuotaMutation } from '$lib/features/redis/queries';
	import {
		profilesQuery,
		userProfilesQuery,
		assignProfileMutation,
		removeProfileMutation
	} from '$lib/features/profiles/queries';
	import { hasRole } from '$lib/types/auth';
	import { m } from '$lib/paraglide/messages';

	let { data } = $props();

	const PAGE_SIZE = 50;
	// Fetch one large page and do all filtering/sorting/pagination client-side
	// (the documented "client-side filtering trap" — the only way to sort the
	// derived roles/quota/last-action columns, which aren't DB columns). The
	// backend caps `limit` at 500, so beyond that users are truncated — surfaced
	// via the `truncated` note below rather than silently hidden.
	const FETCH_LIMIT = 500;
	// Client-side pagination page index (0-based) over the filtered+sorted rows.
	let page = $state(0);

	// ---- Filter + sort state ---------------------------------------------
	let searchText = $state('');
	let roleFilter = $state<'all' | 'user' | 'admin' | 'superuser'>('all');
	let statusFilter = $state<'all' | 'active' | 'inactive'>('all');
	type SortField = 'name' | 'email' | 'roles' | 'status' | 'quota' | 'created' | 'login' | 'action';
	let sortField = $state<SortField>('created');
	let sortDir = $state<'asc' | 'desc'>('desc');

	const qc = useQueryClient();
	// Constant query key — filter/sort/pagination never touch the fetch, so
	// typing in a filter box never triggers a server refetch.
	const users = createUserListQuery({ limit: FETCH_LIMIT });
	const createMut = createUserMutation();
	const updateMut = updateUserMutation();
	const deleteMut = deleteUserMutation();
	const resetQuotaMut = resetUserQuotaMutation();
	const sendResetMut = sendPasswordResetMutation();

	const isAdmin = $derived(hasRole(data.user, 'admin'));

	// ---- Profile assignment (edit dialog) --------------------------------
	const allProfilesQ = profilesQuery();
	const assignMut = assignProfileMutation();
	const removeMut = removeProfileMutation();
	// Only a superuser may grant/revoke the superuser role (enforced server-side too).
	const canManageSuperuser = $derived(hasRole(data.user, 'superuser'));

	/** Compact date + time, or an em-dash when the timestamp is null (never happened). */
	function fmtDateTime(iso: string | null): string {
		if (!iso) return '—';
		const d = new Date(iso);
		return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString();
	}

	// ---- Dialog state ------------------------------------------------
	let dialog = $state<HTMLDialogElement | null>(null);
	let editingId = $state<string | null>(null);

	// The editing user's current profile assignments (declared after editingId).
	let userProfilesQ = $derived(editingId ? userProfilesQuery(editingId) : null);
	let assignedIds = $derived(new Set(($userProfilesQ?.data ?? []).map((a) => a.profile_id)));

	async function toggleProfile(profileId: string, assign: boolean) {
		if (!editingId) return;
		try {
			if (assign) await $assignMut.mutateAsync({ userId: editingId, profileId });
			else await $removeMut.mutateAsync({ userId: editingId, profileId });
		} catch {
			// non-critical; query invalidation reflects the true state
		}
	}

	let form = $state({
		email: '',
		display_name: '',
		password: '',
		is_active: true,
		is_admin: false,
		is_superuser: false,
		quota_disabled: false,
		daily_token_limit: null as number | null,
		request_limit: null as number | null
	});
	let formError = $state('');
	let confirmDeleteId = $state<string | null>(null);
	// Password-reset (email) confirm + transient per-user status banner (also
	// carries a refused delete).
	let confirmResetPwId = $state<string | null>(null);
	let rowStatus = $state<{ id: string; ok: boolean; message: string } | null>(null);

	function showRowStatus(id: string, ok: boolean, message: string) {
		rowStatus = { id, ok, message };
		// Auto-clear the banner after a few seconds.
		setTimeout(() => {
			if (rowStatus?.id === id) rowStatus = null;
		}, 6000);
	}

	async function sendPasswordReset(id: string) {
		confirmResetPwId = null;
		try {
			// pacod-id mails the link.
			const res = await $sendResetMut.mutateAsync(id);
			showRowStatus(id, true, m.users_reset_sent({ email: res.email }));
		} catch (e) {
			showRowStatus(id, false, userErrorMessage(e, m.users_reset_failed()));
		}
	}

	// Live quota counters for the user being edited (Redis-backed; null when creating).
	let editingUsage = $state<{ used: number; limit: number } | null>(null);
	// Activity timestamps for the user being edited (null when creating).
	let editingActivity = $state<{
		lastLogin: string | null;
		lastAction: string | null;
	} | null>(null);
	// The user as loaded, to send only what the form changed: identity fields and
	// roles go to pacod-id, which refuses what the caller may not change.
	let editingUser = $state<User | null>(null);
	let confirmResetQuota = $state(false);
	let resetQuotaError = $state('');

	function blankForm() {
		return {
			email: '',
			display_name: '',
			password: '',
			is_active: true,
			is_admin: false,
			is_superuser: false,
			quota_disabled: false,
			daily_token_limit: null as number | null,
			request_limit: null as number | null
		};
	}

	function openCreate() {
		editingId = null;
		editingUser = null;
		form = blankForm();
		formError = '';
		editingUsage = null;
		editingActivity = null;
		confirmResetQuota = false;
		resetQuotaError = '';
		dialog?.showModal();
	}

	function openEdit(user: User) {
		editingId = user.id;
		editingUser = user;
		form = {
			email: user.email,
			display_name: user.display_name,
			password: '',
			is_active: user.is_active,
			is_admin: user.roles.includes('admin'),
			is_superuser: user.roles.includes('superuser'),
			quota_disabled: user.quota_disabled,
			daily_token_limit: user.daily_token_limit,
			request_limit: user.request_limit
		};
		formError = '';
		editingUsage = { used: user.daily_tokens_used, limit: user.effective_daily_limit };
		editingActivity = {
			lastLogin: user.last_login_at,
			lastAction: user.last_action_at
		};
		confirmResetQuota = false;
		resetQuotaError = '';
		dialog?.showModal();
	}

	function closeDialog() {
		dialog?.close();
		formError = '';
		confirmResetQuota = false;
		resetQuotaError = '';
	}

	async function resetQuota() {
		if (editingId === null) return;
		resetQuotaError = '';
		try {
			await $resetQuotaMut.mutateAsync(editingId);
			// Refresh the users list so the live token counter reflects the reset.
			await qc.invalidateQueries({ queryKey: userKeys.lists() });
			editingUsage = { used: 0, limit: editingUsage?.limit ?? 0 };
			confirmResetQuota = false;
		} catch (e: unknown) {
			resetQuotaError = userErrorMessage(e, m.users_reset_quota_failed());
		}
	}

	async function submitForm() {
		formError = '';
		try {
			// Empty number input binds to null/NaN — normalise to null (= use global default).
			const limit =
				form.daily_token_limit != null && Number.isFinite(form.daily_token_limit)
					? form.daily_token_limit
					: null;
			const reqLimit =
				form.request_limit != null && Number.isFinite(form.request_limit)
					? form.request_limit
					: null;
			if (editingId !== null) {
				const before = editingUser;
				const roles = grantedRoles(form.is_admin, form.is_superuser);
				const patch: UserPatch = {
					quota_disabled: form.quota_disabled,
					daily_token_limit: limit,
					request_limit: reqLimit
				};
				if (!before || form.email !== before.email) patch.email = form.email;
				if (!before || form.display_name !== before.display_name)
					patch.display_name = form.display_name;
				if (!before || form.is_active !== before.is_active) patch.is_active = form.is_active;
				if (!before || !sameGrantedRoles(roles, before.roles)) patch.roles = roles;
				await $updateMut.mutateAsync({ id: editingId, patch });
			} else {
				await $createMut.mutateAsync({
					email: form.email,
					display_name: form.display_name,
					password: form.password || undefined,
					is_active: form.is_active,
					quota_disabled: form.quota_disabled,
					daily_token_limit: limit,
					request_limit: reqLimit
				});
			}
			closeDialog();
		} catch (e: unknown) {
			formError = userErrorMessage(e, m.common_unexpected_error());
		}
	}

	async function confirmDelete(id: string) {
		try {
			await $deleteMut.mutateAsync(id);
			confirmDeleteId = null;
		} catch (e: unknown) {
			confirmDeleteId = null;
			showRowStatus(id, false, userErrorMessage(e, m.common_unexpected_error()));
		}
	}

	// ---- Filtering / sorting / pagination (all client-side) --------------

	/** Rank roles so the Roles column has a deterministic sort order. */
	function roleRank(roles: string[]): number {
		if (roles.includes('superuser')) return 3;
		if (roles.includes('admin')) return 2;
		if (roles.includes('user')) return 1;
		return 0;
	}

	/** Quota usage ratio; null for exempt (disabled) users so they sort last. */
	function quotaValue(u: User): number | null {
		if (u.quota_disabled) return null;
		return u.effective_daily_limit > 0
			? u.daily_tokens_used / u.effective_daily_limit
			: u.daily_tokens_used;
	}

	/** Epoch ms for a nullable ISO timestamp; null (→ sorts last) when absent/invalid. */
	function timeValue(iso: string | null): number | null {
		if (!iso) return null;
		const t = new Date(iso).getTime();
		return Number.isNaN(t) ? null : t;
	}

	function sortValue(u: User, field: SortField): string | number | null {
		switch (field) {
			case 'name':
				return u.display_name.toLowerCase();
			case 'email':
				return u.email.toLowerCase();
			case 'roles':
				return roleRank(u.roles);
			case 'status':
				return u.is_active ? 1 : 0;
			case 'quota':
				return quotaValue(u);
			case 'created':
				return timeValue(u.created_at);
			case 'login':
				return timeValue(u.last_login_at);
			case 'action':
				return timeValue(u.last_action_at);
		}
	}

	/** Compare two users by the active field; null/absent values always sort last. */
	function compareUsers(a: User, b: User): number {
		const va = sortValue(a, sortField);
		const vb = sortValue(b, sortField);
		const aNull = va === null || va === undefined;
		const bNull = vb === null || vb === undefined;
		if (aNull && bNull) return 0;
		if (aNull) return 1; // nulls last, regardless of direction
		if (bNull) return -1;
		const dir = sortDir === 'asc' ? 1 : -1;
		if (va < vb) return -1 * dir;
		if (va > vb) return 1 * dir;
		return 0;
	}

	const filtered = $derived(
		($users.data ?? []).filter((u) => {
			if (statusFilter === 'active' && !u.is_active) return false;
			if (statusFilter === 'inactive' && u.is_active) return false;
			if (roleFilter !== 'all' && !u.roles.includes(roleFilter)) return false;
			const q = searchText.trim().toLowerCase();
			if (q && !`${u.display_name} ${u.email} ${u.id}`.toLowerCase().includes(q)) return false;
			return true;
		})
	);
	const sorted = $derived([...filtered].sort(compareUsers));
	const totalFiltered = $derived(sorted.length);
	// If the filtered set shrinks (filter change or refetch) below the requested
	// page, show the first page — a derived clamp rather than an effect that
	// writes `page` back (an effect reading and writing the same state).
	const pageSafe = $derived(page > 0 && page * PAGE_SIZE >= totalFiltered ? 0 : page);
	const paged = $derived(sorted.slice(pageSafe * PAGE_SIZE, pageSafe * PAGE_SIZE + PAGE_SIZE));

	const hasFilters = $derived(
		searchText.trim() !== '' || roleFilter !== 'all' || statusFilter !== 'all'
	);
	// The backend caps the fetch at 500; warn when we may not have every user.
	const truncated = $derived(($users.data?.length ?? 0) >= FETCH_LIMIT);

	function resetPage() {
		page = 0;
	}
	function clearFilters() {
		searchText = '';
		roleFilter = 'all';
		statusFilter = 'all';
		page = 0;
	}
	function toggleSort(field: SortField) {
		if (sortField === field) {
			sortDir = sortDir === 'asc' ? 'desc' : 'asc';
		} else {
			sortField = field;
			// Timestamps default to newest-first; text/rank fields to ascending.
			sortDir = field === 'created' || field === 'login' || field === 'action' ? 'desc' : 'asc';
		}
		page = 0;
	}
	function prevPage() {
		if (pageSafe > 0) page = pageSafe - 1;
	}
	function nextPage() {
		if ((pageSafe + 1) * PAGE_SIZE < totalFiltered) page = pageSafe + 1;
	}
</script>

<svelte:head><title>{m.users_title()}</title></svelte:head>

<div class="space-y-6">
	<!-- Header -->
	<div class="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between sm:gap-4">
		<div>
			<h1 class="text-2xl font-semibold tracking-tight">{m.nav_users()}</h1>
			<p class="mt-1 text-sm text-[var(--color-text-muted)]">
				{m.users_intro()}
			</p>
		</div>
		{#if isAdmin}
			<Button size="sm" onclick={openCreate} class="w-full sm:w-auto">
				<Plus size={14} />
				{m.users_new()}
			</Button>
		{/if}
	</div>

	<!-- Filters -->
	<div
		class="flex flex-col flex-wrap gap-2 rounded-xl border p-3 sm:flex-row sm:items-center"
		style="background: var(--color-surface-2); border-color: var(--color-border);"
	>
		<label
			class="flex h-10 w-full items-center gap-2 rounded-lg border px-3 transition-colors focus-within:border-[var(--color-brand)] focus-within:shadow-[0_0_0_3px_color-mix(in_oklch,var(--color-brand)_18%,transparent)] sm:w-64"
			style="background: var(--color-surface-2); border-color: var(--color-border);"
		>
			<Search size={13} class="shrink-0 text-[var(--color-text-muted)]" />
			<input
				type="text"
				placeholder={m.users_search_ph()}
				bind:value={searchText}
				oninput={resetPage}
				class="noborder min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-[var(--color-text-muted)] placeholder:opacity-70"
			/>
		</label>
		<select
			bind:value={roleFilter}
			onchange={resetPage}
			class="field-input h-10 w-full sm:w-40"
			aria-label={m.account_roles()}
		>
			<option value="all">{m.users_filter_all_roles()}</option>
			<option value="user">{m.users_role_user()}</option>
			<option value="admin">{m.users_role_admin()}</option>
			<option value="superuser">{m.users_role_superuser()}</option>
		</select>
		<select
			bind:value={statusFilter}
			onchange={resetPage}
			class="field-input h-10 w-full sm:w-40"
			aria-label={m.common_status()}
		>
			<option value="all">{m.users_filter_all_status()}</option>
			<option value="active">{m.account_active()}</option>
			<option value="inactive">{m.account_inactive()}</option>
		</select>
		{#if hasFilters}
			<Button variant="ghost" size="sm" onclick={clearFilters}>
				<X size={14} />
				{m.common_clear()}
			</Button>
		{/if}
	</div>

	<!-- Sortable column header -->
	{#snippet sortTh(field: SortField, label: string, thClass = '')}
		<th class={thClass}>
			<button
				type="button"
				onclick={() => toggleSort(field)}
				class="inline-flex items-center gap-1 font-[inherit] whitespace-nowrap text-[inherit] transition-colors hover:text-[var(--color-text)]"
				aria-label={label}
			>
				<span>{label}</span>
				{#if sortField === field}
					{#if sortDir === 'asc'}<ArrowUp size={12} />{:else}<ArrowDown size={12} />{/if}
				{:else}
					<ArrowUpDown size={12} class="opacity-40" />
				{/if}
			</button>
		</th>
	{/snippet}

	<!-- Table -->
	<div class="overflow-hidden rounded-xl border" style="border-color: var(--color-border);">
		<div class="overflow-x-auto">
			<table class="data-table data-table-stack">
				<thead>
					<tr>
						{@render sortTh('name', m.profiles_col_name())}
						{@render sortTh('email', m.account_email())}
						{@render sortTh('roles', m.account_roles())}
						{@render sortTh('status', m.common_status())}
						{@render sortTh('quota', m.users_col_quota())}
						{@render sortTh('created', m.collections_col_created())}
						{@render sortTh('login', m.users_col_last_login())}
						{@render sortTh('action', m.users_col_last_action())}
						{#if isAdmin}<th></th>{/if}
					</tr>
				</thead>
				<tbody>
					{#if $users.isPending}
						<tr>
							<td colspan="9" class="py-12 text-center text-sm text-[var(--color-text-muted)]">
								{m.users_loading()}
							</td>
						</tr>
					{:else if $users.isError}
						<tr>
							<td colspan="9" class="py-12 text-center text-sm" style="color: var(--color-danger);">
								{m.users_load_failed()}
							</td>
						</tr>
					{:else if paged.length > 0}
						{#each paged as user (user.id)}
							<tr>
								<td data-label={m.profiles_col_name()} class="font-medium">{user.display_name}</td>
								<td data-label={m.account_email()} class="text-[var(--color-text-muted)]"
									>{user.email}</td
								>
								<td data-label={m.account_roles()}>
									<div class="flex flex-wrap justify-end gap-1 sm:justify-start">
										{#each user.roles as role (role)}
											<span
												class="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium"
												style={role === 'superuser'
													? 'background: var(--color-danger-soft); color: var(--color-danger);'
													: role === 'admin'
														? 'background: var(--color-brand-soft); color: var(--color-brand);'
														: 'background: var(--color-surface-3); color: var(--color-text-muted);'}
											>
												{role.charAt(0).toUpperCase() + role.slice(1)}
											</span>
										{:else}
											<span class="text-xs text-[var(--color-text-muted)]">—</span>
										{/each}
									</div>
								</td>
								<td data-label={m.common_status()}>
									<span
										class="inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium"
										style={user.is_active
											? 'background: var(--color-success-soft); color: var(--color-success);'
											: 'background: var(--color-surface-3); color: var(--color-text-muted);'}
									>
										<span
											class="h-1.5 w-1.5 rounded-full"
											style={user.is_active
												? 'background: var(--color-success);'
												: 'background: var(--color-text-muted);'}
										></span>
										{user.is_active ? m.account_active() : m.account_inactive()}
									</span>
								</td>
								<td data-label={m.users_col_quota()}>
									{#if user.quota_disabled}
										<span
											class="inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium"
											style="background: var(--color-surface-3); color: var(--color-text-muted);"
										>
											{m.users_quota_disabled()}
										</span>
									{:else}
										<span
											class="text-xs tabular-nums"
											style={user.daily_tokens_used >= user.effective_daily_limit
												? 'color: var(--color-danger);'
												: 'color: var(--color-text-muted);'}
											title={m.users_quota_title()}
										>
											{user.daily_tokens_used.toLocaleString()} / {user.effective_daily_limit.toLocaleString()}
										</span>
									{/if}
								</td>
								<td
									data-label={m.collections_col_created()}
									class="text-[var(--color-text-muted)] tabular-nums"
								>
									{new Date(user.created_at).toLocaleDateString()}
								</td>
								<td
									data-label={m.users_col_last_login()}
									class="text-xs text-[var(--color-text-muted)] tabular-nums"
									title={user.last_login_at ?? m.users_never_login()}
								>
									{fmtDateTime(user.last_login_at)}
								</td>
								<td
									data-label={m.users_col_last_action()}
									class="text-xs text-[var(--color-text-muted)] tabular-nums"
									title={user.last_action_at ?? m.users_no_activity()}
								>
									{fmtDateTime(user.last_action_at)}
								</td>
								{#if isAdmin}
									<td data-label="" class="w-28">
										{#if confirmDeleteId === user.id}
											<div class="flex items-center justify-end gap-1">
												<button
													onclick={() => confirmDelete(user.id)}
													class="touch-target rounded-lg p-1.5 transition-colors"
													style="color: var(--color-danger);"
													aria-label={m.common_confirm_delete()}
													title={m.common_confirm_delete()}
												>
													<Check size={14} />
												</button>
												<button
													onclick={() => (confirmDeleteId = null)}
													class="touch-target rounded-lg p-1.5 text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-3)]"
													aria-label={m.common_cancel()}
													title={m.common_cancel()}
												>
													<X size={14} />
												</button>
											</div>
										{:else if confirmResetPwId === user.id}
											<div
												class="flex items-center justify-end gap-1"
												title={m.users_send_reset_q()}
											>
												<button
													onclick={() => sendPasswordReset(user.id)}
													class="touch-target rounded-lg p-1.5 transition-colors"
													style="color: var(--color-brand);"
													aria-label={m.users_confirm_send_reset()}
													title={m.users_send_reset_email()}
												>
													<Check size={14} />
												</button>
												<button
													onclick={() => (confirmResetPwId = null)}
													class="touch-target rounded-lg p-1.5 text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-3)]"
													aria-label={m.common_cancel()}
													title={m.common_cancel()}
												>
													<X size={14} />
												</button>
											</div>
										{:else}
											<div class="flex items-center justify-end gap-1">
												<button
													onclick={() => openEdit(user)}
													class="touch-target rounded-lg p-1.5 text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-3)] hover:text-[var(--color-text)]"
													aria-label={m.common_edit()}
													title={m.common_edit()}
												>
													<Pencil size={14} />
												</button>
												<a
													href="/analytics/users/{encodeURIComponent(user.id)}"
													class="touch-target rounded-lg p-1.5 text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-3)] hover:text-[var(--color-text)]"
													aria-label={m.analytics_users_usage_link()}
													title={m.analytics_users_usage_link()}
												>
													<ChartColumn size={14} />
												</a>
												<button
													onclick={() => (confirmResetPwId = user.id)}
													class="touch-target rounded-lg p-1.5 text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-3)] hover:text-[var(--color-brand)]"
													aria-label={m.users_send_reset_title()}
													title={m.users_send_reset_title()}
												>
													<KeyRound size={14} />
												</button>
												<button
													onclick={() => (confirmDeleteId = user.id)}
													class="touch-target rounded-lg p-1.5 text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-danger)]"
													aria-label={m.common_delete()}
													title={m.common_delete()}
												>
													<Trash2 size={14} />
												</button>
											</div>
										{/if}
										{#if rowStatus?.id === user.id}
											<p
												class="mt-1 text-right text-[11px] sm:text-left"
												style="color: {rowStatus.ok
													? 'var(--color-text-muted)'
													: 'var(--color-danger)'};"
											>
												{rowStatus.message}
											</p>
										{/if}
									</td>
								{/if}
							</tr>
						{/each}
					{:else if hasFilters}
						<tr>
							<td colspan="9" class="py-12 text-center text-sm text-[var(--color-text-muted)]">
								{m.users_no_results()}
							</td>
						</tr>
					{:else}
						<tr>
							<td colspan="9" class="py-12 text-center text-sm text-[var(--color-text-muted)]">
								{m.users_empty()}
							</td>
						</tr>
					{/if}
				</tbody>
			</table>
		</div>
	</div>

	<!-- Truncation note — the backend returns at most FETCH_LIMIT users. -->
	{#if truncated}
		<p class="text-xs" style="color: var(--color-text-muted);">
			{m.users_truncated({ limit: FETCH_LIMIT })}
		</p>
	{/if}

	<!-- Pagination -->
	{#if totalFiltered > 0}
		<div class="flex items-center justify-between text-sm text-[var(--color-text-muted)]">
			<span>
				{m.common_showing_range({
					from: pageSafe * PAGE_SIZE + 1,
					to: pageSafe * PAGE_SIZE + paged.length
				})} · {totalFiltered}
			</span>
			<div class="flex gap-2">
				<Button variant="secondary" size="sm" onclick={prevPage} disabled={pageSafe === 0}>
					{m.users_previous()}
				</Button>
				<Button
					variant="secondary"
					size="sm"
					onclick={nextPage}
					disabled={(pageSafe + 1) * PAGE_SIZE >= totalFiltered}
				>
					{m.common_next()}
				</Button>
			</div>
		</div>
	{/if}
</div>

<!-- ------------------------------------------------------------------ -->
<!-- Create / Edit dialog -->
<!-- ------------------------------------------------------------------ -->
<dialog
	bind:this={dialog}
	class="mx-auto max-h-[90dvh] w-[calc(100%-2rem)] max-w-lg overflow-y-auto rounded-2xl p-0 shadow-2xl"
	style="background: var(--color-surface); border: 1px solid var(--color-border);"
>
	<form
		onsubmit={(e) => {
			e.preventDefault();
			submitForm();
		}}
	>
		<!-- Header -->
		<div
			class="flex items-center justify-between border-b px-6 py-4"
			style="border-color: var(--color-border);"
		>
			<h2 class="text-base font-semibold">
				{editingId ? m.users_edit_title() : m.users_new_title()}
			</h2>
			<button
				type="button"
				onclick={closeDialog}
				class="rounded-lg p-1.5 text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-2)] hover:text-[var(--color-text)]"
				aria-label={m.common_close()}
			>
				<X size={16} />
			</button>
		</div>

		<!-- Body -->
		<div class="space-y-4 px-6 py-5">
			<!-- Name / Email row -->
			<div class="grid grid-cols-1 gap-3 sm:grid-cols-2">
				<div>
					<label class="field-label" for="user-name">{m.users_field_name()}</label>
					<input
						id="user-name"
						type="text"
						bind:value={form.display_name}
						required
						minlength="1"
						maxlength="128"
						placeholder={m.users_field_name_ph()}
						class="field-input"
					/>
				</div>
				<div>
					<label class="field-label" for="user-email">{m.users_field_email()}</label>
					<input
						id="user-email"
						type="email"
						bind:value={form.email}
						required
						placeholder="user@example.com"
						class="field-input"
					/>
				</div>
			</div>

			{#if !editingId}
				<!-- Password — only on create -->
				<div>
					<label class="field-label" for="user-password">{m.login_password()}</label>
					<input
						id="user-password"
						type="password"
						bind:value={form.password}
						minlength="8"
						placeholder={m.users_field_password_ph()}
						class="field-input"
					/>
					<p class="mt-1 text-xs text-[var(--color-text-muted)]">{m.users_password_hint()}</p>
				</div>
			{/if}

			<!-- Toggles -->
			<div class="flex flex-wrap gap-6 pt-1">
				<label class="field-label flex cursor-pointer items-center gap-2.5">
					<input type="checkbox" bind:checked={form.is_active} class="h-4 w-4 rounded" />
					<span class="text-sm font-medium">{m.account_active()}</span>
				</label>
				<label class="field-label flex cursor-pointer items-center gap-2.5">
					<input
						type="checkbox"
						bind:checked={form.is_admin}
						disabled={form.is_superuser}
						class="h-4 w-4 rounded"
					/>
					<span class="text-sm font-medium">{m.users_admin_role()}</span>
				</label>
				<label
					class="field-label flex items-center gap-2.5"
					class:cursor-pointer={canManageSuperuser}
					class:opacity-60={!canManageSuperuser}
				>
					<input
						type="checkbox"
						bind:checked={form.is_superuser}
						disabled={!canManageSuperuser}
						class="h-4 w-4 rounded"
					/>
					<span class="text-sm font-medium">{m.users_superuser()}</span>
				</label>
			</div>
			<p class="-mt-2 text-xs text-[var(--color-text-muted)]">
				{m.users_superuser_hint()}
				{#if !canManageSuperuser}
					{m.users_superuser_only()}
				{/if}
			</p>

			{#if editingId}
				<!-- Assigned profiles (agents this user may chat with) -->
				<div
					class="rounded-lg border p-3"
					style="border-color: var(--color-border); background: var(--color-surface-2);"
				>
					<div class="field-label !mb-1.5">{m.users_assigned_profiles()}</div>
					{#if ($allProfilesQ.data ?? []).length === 0}
						<p class="text-xs text-[var(--color-text-muted)]">{m.users_no_profiles()}</p>
					{:else}
						<div class="grid grid-cols-1 gap-1.5 sm:grid-cols-2">
							{#each $allProfilesQ.data ?? [] as p (p.id)}
								<label class="flex cursor-pointer items-center gap-2 text-sm">
									<input
										type="checkbox"
										class="h-4 w-4 rounded"
										checked={assignedIds.has(p.id)}
										onchange={(e) =>
											toggleProfile(p.id, (e.currentTarget as HTMLInputElement).checked)}
									/>
									<span class="truncate">{p.name}</span>
									{#if !p.is_active}<span class="text-[10px] text-[var(--color-text-faint)]"
											>{m.common_inactive_lc()}</span
										>{/if}
								</label>
							{/each}
						</div>
						<p class="mt-1.5 text-[11px] text-[var(--color-text-muted)]">
							{m.users_profiles_note()}
						</p>
					{/if}
				</div>
			{/if}

			{#if editingId && editingActivity}
				<!-- Activity (read-only): last interactive login + last guarded request -->
				<div
					class="grid grid-cols-1 gap-3 rounded-lg border p-3 sm:grid-cols-2"
					style="border-color: var(--color-border); background: var(--color-surface-2);"
				>
					<div>
						<div class="field-label !mb-0.5">{m.users_col_last_login()}</div>
						<div
							class="text-sm tabular-nums"
							title={editingActivity.lastLogin ?? m.users_never_login()}
						>
							{fmtDateTime(editingActivity.lastLogin)}
						</div>
					</div>
					<div>
						<div class="field-label !mb-0.5">{m.users_col_last_action()}</div>
						<div
							class="text-sm tabular-nums"
							title={editingActivity.lastAction ?? m.users_no_activity()}
						>
							{fmtDateTime(editingActivity.lastAction)}
						</div>
					</div>
					<a
						href="/analytics/users/{encodeURIComponent(editingId)}"
						class="inline-flex items-center gap-1 text-sm hover:underline sm:col-span-2"
						style="color: var(--color-brand);"
					>
						<ChartColumn size={14} />
						{m.analytics_users_usage_link_long()}
					</a>
				</div>
			{/if}

			<!-- Quota controls -->
			<div
				class="space-y-3 rounded-lg border p-3"
				style="border-color: var(--color-border); background: var(--color-surface-2);"
			>
				<label class="flex cursor-pointer items-center gap-2.5">
					<input type="checkbox" bind:checked={form.quota_disabled} class="h-4 w-4 rounded" />
					<span class="text-sm font-medium text-[var(--color-text-muted)]"
						>{m.users_quota_disabled_toggle()}</span
					>
					<span class="text-xs text-[var(--color-text-muted)]">{m.users_quota_exempt()}</span>
				</label>
				<div>
					<label class="field-label" for="user-token-limit">{m.users_daily_limit()}</label>
					<input
						id="user-token-limit"
						type="number"
						min="0"
						step="100000"
						bind:value={form.daily_token_limit}
						placeholder={m.users_daily_limit_ph()}
						class="field-input"
						disabled={form.quota_disabled}
					/>
				</div>
				<div>
					<label class="field-label" for="user-request-limit">{m.users_request_limit()}</label>
					<input
						id="user-request-limit"
						type="number"
						min="0"
						step="100"
						bind:value={form.request_limit}
						placeholder={m.users_request_limit_ph()}
						class="field-input"
						disabled={form.quota_disabled}
					/>
				</div>

				{#if editingId && editingUsage}
					<!-- Live quota counters + reset (Redis-backed) -->
					<div
						class="flex items-center justify-between gap-3 border-t pt-3"
						style="border-color: var(--color-border);"
					>
						<div class="min-w-0">
							<div class="field-label">{m.users_used_today()}</div>
							<div
								class="text-sm tabular-nums"
								style={editingUsage.used >= editingUsage.limit && editingUsage.limit > 0
									? 'color: var(--color-danger);'
									: 'color: var(--color-text-muted);'}
							>
								{m.users_tokens_used({
									used: editingUsage.used.toLocaleString(),
									limit: editingUsage.limit.toLocaleString()
								})}
							</div>
						</div>
						{#if confirmResetQuota}
							<div class="flex items-center gap-1">
								<button
									type="button"
									onclick={resetQuota}
									disabled={$resetQuotaMut.isPending}
									class="rounded-lg p-1.5 transition-colors disabled:opacity-50"
									style="color: var(--color-danger);"
									aria-label={m.users_confirm_reset_quota()}
									title={m.redis_confirm_reset()}
								>
									<Check size={14} />
								</button>
								<button
									type="button"
									onclick={() => (confirmResetQuota = false)}
									class="rounded-lg p-1.5 text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-3)]"
									aria-label={m.users_cancel_reset_quota()}
									title={m.common_cancel()}
								>
									<X size={14} />
								</button>
							</div>
						{:else}
							<button
								type="button"
								onclick={() => {
									resetQuotaError = '';
									confirmResetQuota = true;
								}}
								class="flex shrink-0 items-center gap-1 rounded-lg px-2 py-1 text-xs text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-danger)]"
								title={m.users_reset_quota_title()}
							>
								<RefreshCw size={12} />
								{m.redis_reset_quota()}
							</button>
						{/if}
					</div>
					{#if resetQuotaError}
						<p class="text-xs" style="color: var(--color-danger);">{resetQuotaError}</p>
					{/if}
				{/if}
			</div>
		</div>

		<!-- Error -->
		{#if formError}
			<div
				class="mx-6 mb-2 rounded-lg px-3 py-2 text-sm"
				style="background: var(--color-danger-soft); color: var(--color-danger);"
			>
				{formError}
			</div>
		{/if}

		<!-- Footer -->
		<div
			class="flex justify-end gap-2 border-t px-6 py-4"
			style="border-color: var(--color-border);"
		>
			<Button type="button" variant="secondary" size="sm" onclick={closeDialog}
				>{m.common_cancel()}</Button
			>
			<Button type="submit" size="sm" disabled={$createMut.isPending || $updateMut.isPending}>
				{editingId ? m.common_save_changes() : m.common_create()}
			</Button>
		</div>
	</form>
</dialog>
