<script lang="ts">
	import { untrack } from 'svelte';
	import type { Snippet } from 'svelte';
	import { page } from '$app/state';
	import SiteFooter from '$lib/components/layout/SiteFooter.svelte';
	import {
		Database,
		Boxes,
		Bot,
		Radar,
		ChartColumn,
		Users,
		LayoutDashboard,
		MessageSquare,
		LogOut,
		Percent,
		Upload,
		Server,
		Megaphone,
		MessagesSquare,
		Cpu,
		FileText,
		Menu,
		X,
		Sun,
		Moon,
		ChevronDown,
		Settings,
		PanelLeft,
		Terminal,
		Zap,
		ClipboardCheck,
		Workflow,
		Waypoints,
		Layers,
		Mail,
		Activity,
		BookMarked
	} from 'lucide-svelte';
	import { selectedProfile } from '$lib/stores/selectedProfile.svelte';
	import { profilesQuery, shortNameFor, type MyProfile } from '$lib/features/profiles/queries';
	import { getLocale } from '$lib/paraglide/runtime';
	import { cn } from '$lib/utils/cn';
	import { userLabel, userInitial, hasRole, type User, type Role } from '$lib/types/auth';
	import { theme } from '$lib/stores/theme.svelte';
	import { sidebar } from '$lib/stores/sidebar.svelte';
	import { m } from '$lib/paraglide/messages';
	import LanguageSwitcher from './LanguageSwitcher.svelte';

	interface Props {
		user: User;
		/** Agents (chat profiles) the user may talk to — one sidebar entry each. */
		myProfiles?: MyProfile[];
		children: Snippet;
	}

	let { user, myProfiles = [], children }: Props = $props();

	// One sidebar entry per agent (profile) the user may chat with. Each links to
	// /chat?agent=<id>; the chat page treats that param as the active agent. When
	// the user has no assigned agents (or the list failed to load) we fall back to
	// a single generic "Chat" link so the chat surface is always reachable.
	let agentItems = $derived(
		myProfiles.length > 0
			? myProfiles.map((p) => ({
					href: `/chat?agent=${encodeURIComponent(p.id)}`,
					label: shortNameFor(p, getLocale()),
					icon: MessageSquare,
					agentId: p.id
				}))
			: [
					{
						href: '/chat',
						label: m.nav_chat(),
						icon: MessageSquare,
						agentId: null as string | null
					}
				]
	);

	// Labels are read via m.*() so they follow the active locale. Derived so the
	// agent list stays in sync; switching language triggers a full reload.
	let navGroups = $derived([
		{
			label: null,
			items: [
				{ href: '/dashboard', label: m.nav_dashboard(), icon: LayoutDashboard },
				...agentItems
			]
		},
		{
			label: m.nav_group_knowledge_base(),
			items: [
				{
					href: '/collections',
					label: m.nav_collections(),
					icon: Database,
					requires: 'admin' as const
				},
				{ href: '/points', label: m.nav_points(), icon: Boxes, requires: 'admin' as const },
				{ href: '/documents', label: m.nav_documents(), icon: Upload, requires: 'admin' as const },
				{ href: '/curated', label: m.nav_curated(), icon: BookMarked, requires: 'admin' as const }
			]
		},
		{
			label: m.nav_group_reference_data(),
			items: [
				{
					href: '/reference-facts',
					label: m.nav_reference_facts(),
					icon: Percent,
					requires: 'admin' as const
				}
			]
		},
		{
			label: m.nav_group_agent(),
			items: [
				{
					href: '/agent-editor',
					label: m.nav_agent_editor(),
					icon: Waypoints,
					requires: 'superuser' as const
				},
				{
					href: '/directives',
					label: m.nav_directives(),
					icon: Megaphone,
					requires: 'admin' as const
				},
				{
					href: '/review-process',
					label: m.nav_review_process(),
					icon: Workflow,
					requires: 'admin' as const
				},
				{ href: '/newsletter', label: m.nav_newsletter(), icon: Mail, requires: 'admin' as const },
				{
					href: '/kb-candidates',
					label: m.nav_review(),
					icon: ClipboardCheck,
					requires: 'admin' as const
				},
				{
					href: '/agent-prompts',
					label: m.nav_prompts(),
					icon: FileText,
					requires: 'admin' as const
				},
				{ href: '/llm-config', label: m.nav_llm_search(), icon: Cpu, requires: 'admin' as const },
				{ href: '/llm-models', label: m.nav_llm_models(), icon: Bot, requires: 'admin' as const },
				{
					href: '/response-cache',
					label: m.nav_response_cache(),
					icon: Zap,
					requires: 'admin' as const
				}
			]
		},
		{
			label: m.nav_group_monitoring(),
			items: [
				{
					href: '/conversations',
					label: m.nav_conversations(),
					icon: MessagesSquare,
					requires: 'admin' as const
				},
				{
					href: '/debug-console',
					label: m.nav_debug_console(),
					icon: Terminal,
					requires: 'admin' as const
				},
				{ href: '/metrics', label: m.nav_metrics(), icon: Activity, requires: 'admin' as const },
				{
					href: '/analytics',
					label: m.nav_analytics(),
					icon: ChartColumn,
					requires: 'admin' as const
				},
				{ href: '/crawlers', label: m.nav_crawlers(), icon: Radar, requires: 'admin' as const }
			]
		},
		{
			label: m.nav_group_administration(),
			items: [
				{ href: '/profiles', label: m.nav_profiles(), icon: Layers, requires: 'admin' as const },
				{ href: '/users', label: m.nav_users(), icon: Users, requires: 'admin' as const },
				{ href: '/redis', label: m.nav_redis(), icon: Server, requires: 'admin' as const }
			]
		}
	]);

	// Admin "active profile" scope selector (topbar). Lists all profiles; null =
	// global (all profiles). Only queried for admins.
	const isAdmin = untrack(() => hasRole(user, 'admin')); // role is fixed for the session
	const profilesQ = profilesQuery(isAdmin);
	let allProfiles = $derived($profilesQ.data ?? []);
	let scopedName = $derived(
		selectedProfile.id
			? (() => {
					const p = allProfiles.find((pp) => pp.id === selectedProfile.id);
					return p ? shortNameFor(p, getLocale()) : m.appshell_profile_fallback();
				})()
			: m.appshell_all_profiles()
	);

	let visibleGroups = $derived(
		navGroups
			.map((group) => ({
				...group,
				items: group.items.filter((item) => {
					const requires = (item as { requires?: Role }).requires;
					return !requires || hasRole(user, requires);
				})
			}))
			.filter((group) => group.items.length > 0)
	);

	let path = $derived(page.url.pathname);
	let sidebarOpen = $state(false);

	// Which agent is currently active on /chat, for highlighting its entry. The
	// chat page redirects bare /chat → ?agent=<last-used>, so the URL param is
	// authoritative; until that redirect lands we fall back to the stored
	// preference (then the first agent) so the highlight still matches.
	let activeAgentId = $derived.by(() => {
		if (path !== '/chat') return null;
		const urlAgent = page.url.searchParams.get('agent');
		if (urlAgent && myProfiles.some((p) => p.id === urlAgent)) return urlAgent;
		if (typeof localStorage !== 'undefined') {
			const stored = localStorage.getItem('chat:profileId');
			if (stored && myProfiles.some((p) => p.id === stored)) return stored;
		}
		return myProfiles[0]?.id ?? null;
	});

	function isActive(item: { href: string; agentId?: string | null }): boolean {
		// Per-agent chat entry: active only when that agent is the selected one.
		if (item.agentId != null) return path === '/chat' && activeAgentId === item.agentId;
		// Generic fallback "Chat" link (no agents assigned).
		if (item.href === '/chat') return path === '/chat';
		return path === item.href || path.startsWith(item.href + '/');
	}

	function closeSidebar() {
		sidebarOpen = false;
	}

	// ---- Account menu (top-right dropdown) ---------------------------
	let userMenuOpen = $state(false);
	let userMenuEl = $state<HTMLDivElement | null>(null);

	// ---- Profile scope selector (admin only) -------------------------
	let profileMenuOpen = $state(false);
	let profileMenuEl = $state<HTMLDivElement | null>(null);

	function pickScope(id: string | null) {
		selectedProfile.set(id);
		profileMenuOpen = false;
	}

	function onWindowClick(e: MouseEvent) {
		if (userMenuOpen && userMenuEl && !userMenuEl.contains(e.target as Node)) {
			userMenuOpen = false;
		}
		if (profileMenuOpen && profileMenuEl && !profileMenuEl.contains(e.target as Node)) {
			profileMenuOpen = false;
		}
	}
	function onWindowKeydown(e: KeyboardEvent) {
		if (e.key === 'Escape') {
			userMenuOpen = false;
			profileMenuOpen = false;
		}
	}
</script>

<svelte:window onclick={onWindowClick} onkeydown={onWindowKeydown} />

<div class="flex h-screen overflow-hidden">
	<!-- Mobile backdrop -->
	{#if sidebarOpen}
		<div
			class="fixed inset-0 z-20 bg-black/60 backdrop-blur-sm lg:hidden"
			role="presentation"
			onclick={closeSidebar}
		></div>
	{/if}

	<!-- ------------------------------------------------------------------ -->
	<!-- Sidebar -->
	<!-- ------------------------------------------------------------------ -->
	<aside
		class={cn(
			'fixed inset-y-0 left-0 z-30 flex w-56 shrink-0 flex-col transition-all duration-200 ease-in-out lg:relative lg:translate-x-0',
			sidebarOpen ? 'translate-x-0' : '-translate-x-full',
			sidebar.collapsed ? 'lg:w-16' : 'lg:w-56'
		)}
		style="background: var(--color-sidebar);"
	>
		<!-- Logo / brand -->
		<div
			class={cn(
				'flex h-14 shrink-0 items-center gap-3 border-b px-4',
				sidebar.collapsed && 'lg:justify-center lg:px-0'
			)}
			style="border-color: var(--color-sidebar-border);"
		>
			<a
				href="/"
				class={cn('flex min-w-0 flex-1 items-center gap-3', sidebar.collapsed && 'lg:flex-none')}
				aria-label={m.app_subtitle()}
			>
				<div
					class="relative flex h-8 w-8 shrink-0 items-center justify-center rounded-[9px]"
					style="background: var(--color-brand);"
					aria-hidden="true"
				>
					<span class="font-display text-[12px] font-extrabold tracking-tight text-white">BG</span>
					<span
						class="absolute -right-0.5 -bottom-0.5 h-2 w-2 rounded-full ring-2"
						style="background: var(--color-gold); --tw-ring-color: var(--color-sidebar);"
					></span>
				</div>
				<div class={cn('min-w-0 flex-1', sidebar.collapsed && 'lg:hidden')}>
					<div
						class="font-display truncate text-sm leading-tight font-bold"
						style="color: var(--color-sidebar-text);"
					>
						PACOD
					</div>
					<div
						class="font-mono text-[10px] leading-tight tracking-[0.14em] uppercase"
						style="color: var(--color-sidebar-muted);"
					>
						{m.app_subtitle()}
					</div>
				</div>
			</a>
			<button
				class="ml-auto rounded-md p-1 lg:hidden"
				style="color: var(--color-sidebar-muted);"
				onclick={closeSidebar}
				aria-label={m.appshell_close_sidebar()}
			>
				<X size={16} />
			</button>
		</div>

		<!-- Navigation -->
		<nav class="flex-1 overflow-y-auto px-2 py-3" aria-label={m.appshell_main_navigation()}>
			{#each visibleGroups as group, groupIndex}
				<div
					class={cn(groupIndex > 0 && 'mt-4')}
					role="group"
					aria-label={group.label ?? undefined}
				>
					{#if group.label}
						{#if sidebar.collapsed}
							<div
								class="mx-2 mb-1 hidden border-t lg:block"
								style="border-color: var(--color-sidebar-border);"
								aria-hidden="true"
							></div>
						{/if}
						<div
							class={cn(
								'px-3 pt-0.5 pb-1 font-mono text-[10px] font-semibold tracking-[0.12em] uppercase',
								sidebar.collapsed && 'lg:hidden'
							)}
							style="color: var(--color-sidebar-muted);"
						>
							{group.label}
						</div>
					{/if}
					<div class="space-y-0.5">
						{#each group.items as item}
							{@const Icon = item.icon}
							{@const active = isActive(item)}
							<a
								href={item.href}
								onclick={closeSidebar}
								class={cn(
									'sidebar-item',
									active && 'active',
									sidebar.collapsed && 'lg:justify-center'
								)}
								aria-current={active ? 'page' : undefined}
								title={sidebar.collapsed ? item.label : undefined}
							>
								{#if active}
									<span
										class="absolute top-1/2 left-0 h-4 w-0.5 -translate-y-1/2 rounded-r-full"
										style="background: var(--color-brand);"
										aria-hidden="true"
									></span>
								{/if}
								<Icon size={16} class="shrink-0" />
								<span class={cn('min-w-0 flex-1 truncate', sidebar.collapsed && 'lg:hidden')}
									>{item.label}</span
								>
							</a>
						{/each}
					</div>
				</div>
			{/each}
		</nav>
	</aside>

	<!-- ------------------------------------------------------------------ -->
	<!-- Main content area -->
	<!-- ------------------------------------------------------------------ -->
	<div class="flex min-w-0 flex-1 flex-col overflow-hidden">
		<!-- Topbar -->
		<header
			class="flex h-14 shrink-0 items-center gap-3 border-b px-4 lg:px-6"
			style="background: var(--color-surface); border-color: var(--color-border);"
		>
			<!-- Mobile hamburger -->
			<button
				class="rounded-lg p-1.5 text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-2)] hover:text-[var(--color-text)] lg:hidden"
				onclick={() => (sidebarOpen = true)}
				aria-label={m.appshell_open_navigation()}
			>
				<Menu size={20} />
			</button>

			<!-- Desktop sidebar fold toggle -->
			<button
				class="hidden rounded-lg p-1.5 text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-2)] hover:text-[var(--color-text)] lg:inline-flex"
				onclick={() => sidebar.toggle()}
				aria-label={sidebar.collapsed ? m.appshell_expand_sidebar() : m.appshell_collapse_sidebar()}
				aria-pressed={sidebar.collapsed}
				title={sidebar.collapsed ? m.appshell_expand_sidebar() : m.appshell_collapse_sidebar()}
			>
				<PanelLeft size={18} />
			</button>

			<div class="flex-1"></div>

			<!-- Admin profile scope selector: global (all) vs one profile -->
			{#if isAdmin}
				<div class="relative min-w-0" bind:this={profileMenuEl}>
					<button
						class="flex max-w-full min-w-0 items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs font-medium text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-2)] hover:text-[var(--color-text)]"
						style="border-color: var(--color-border);"
						onclick={() => (profileMenuOpen = !profileMenuOpen)}
						title={m.appshell_scope_to_profile()}
					>
						<Layers size={14} class="shrink-0" />
						<span class="max-w-[6rem] truncate sm:max-w-[10rem]">{scopedName}</span>
						<ChevronDown size={13} class="shrink-0" />
					</button>
					{#if profileMenuOpen}
						<div
							class="fixed top-[3.5rem] right-2 z-30 max-h-80 w-[calc(100vw-1rem)] max-w-xs overflow-y-auto rounded-lg border py-1 shadow-lg sm:absolute sm:top-auto sm:right-0 sm:mt-1 sm:w-56 sm:max-w-none"
							style="background: var(--color-surface-2); border-color: var(--color-border);"
						>
							<button
								class="flex w-full items-center px-3 py-1.5 text-left text-xs transition-colors hover:bg-[var(--color-surface-3)]"
								style={selectedProfile.id === null
									? 'color: var(--color-brand);'
									: 'color: var(--color-text);'}
								onclick={() => pickScope(null)}
							>
								{m.appshell_all_profiles_global()}
							</button>
							<div class="my-1 border-t" style="border-color: var(--color-border);"></div>
							{#each allProfiles as p (p.id)}
								<button
									class="flex w-full items-center px-3 py-1.5 text-left text-xs transition-colors hover:bg-[var(--color-surface-3)]"
									style={selectedProfile.id === p.id
										? 'color: var(--color-brand);'
										: 'color: var(--color-text);'}
									onclick={() => pickScope(p.id)}
								>
									<span class="truncate">{shortNameFor(p, getLocale())}</span>
									{#if !p.is_active}
										<span class="ml-auto text-[10px]" style="color: var(--color-text-faint);"
											>{m.appshell_inactive()}</span
										>
									{/if}
								</button>
							{/each}
						</div>
					{/if}
				</div>
			{/if}

			<!-- Language switcher (flag-only) -->
			<LanguageSwitcher />

			<!-- Theme toggle -->
			<button
				class="rounded-lg p-1.5 text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-2)] hover:text-[var(--color-text)]"
				onclick={() => theme.toggle()}
				aria-label={theme.current === 'dark'
					? m.appshell_theme_to_light()
					: m.appshell_theme_to_dark()}
			>
				{#if theme.current === 'dark'}
					<Sun size={18} />
				{:else}
					<Moon size={18} />
				{/if}
			</button>

			<!-- Account menu -->
			<div class="relative" bind:this={userMenuEl}>
				<button
					type="button"
					onclick={() => (userMenuOpen = !userMenuOpen)}
					class="flex items-center gap-2.5 rounded-full py-1 pr-1.5 pl-2.5 transition-colors hover:bg-[var(--color-surface-2)]"
					aria-haspopup="menu"
					aria-expanded={userMenuOpen}
					aria-label={m.appshell_account_menu()}
				>
					<div class="hidden text-right sm:block">
						<div class="text-sm leading-none font-medium">{userLabel(user)}</div>
						{#if user.email}
							<div class="mt-0.5 text-xs text-[var(--color-text-muted)]">{user.email}</div>
						{/if}
					</div>
					<div
						class="grid h-8 w-8 place-items-center rounded-full text-sm font-semibold"
						style="background: var(--color-brand-soft); color: var(--color-brand);"
						aria-hidden="true"
					>
						{userInitial(user)}
					</div>
					<ChevronDown
						size={14}
						class={cn(
							'text-[var(--color-text-muted)] transition-transform duration-150',
							userMenuOpen && 'rotate-180'
						)}
					/>
				</button>

				{#if userMenuOpen}
					<div
						role="menu"
						aria-label={m.appshell_account()}
						class="absolute top-full right-0 z-40 mt-2 w-56 overflow-hidden rounded-xl border shadow-lg"
						style="background: var(--color-surface); border-color: var(--color-border);"
					>
						<div class="border-b px-3 py-2.5" style="border-color: var(--color-border);">
							<div class="truncate text-sm font-medium">{userLabel(user)}</div>
							{#if user.email}
								<div class="truncate text-xs text-[var(--color-text-muted)]">{user.email}</div>
							{/if}
						</div>
						<div class="p-1.5">
							<a
								href="/account"
								role="menuitem"
								onclick={() => (userMenuOpen = false)}
								class="flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm transition-colors hover:bg-[var(--color-surface-2)]"
							>
								<Settings size={15} class="shrink-0 text-[var(--color-text-muted)]" />
								{m.appshell_account_settings()}
							</a>
							<form method="POST" action="/auth/logout">
								<button
									type="submit"
									role="menuitem"
									class="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm transition-colors hover:bg-[var(--color-danger-soft)]"
									style="color: var(--color-danger);"
								>
									<LogOut size={15} class="shrink-0" />
									{m.appshell_sign_out()}
								</button>
							</form>
						</div>
					</div>
				{/if}
			</div>
		</header>

		<!-- Page content -->
		<main class="flex-1 overflow-auto bg-[var(--color-surface)] p-4 sm:p-6">
			{@render children()}
		</main>

		<SiteFooter />
	</div>
</div>
