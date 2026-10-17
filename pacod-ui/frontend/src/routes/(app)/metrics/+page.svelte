<script lang="ts">
	import {
		Activity,
		Server,
		Globe,
		Bot,
		ChartColumn,
		ExternalLink,
		Waypoints,
		Boxes
	} from 'lucide-svelte';
	import { theme } from '$lib/stores/theme.svelte';
	import { m } from '$lib/paraglide/messages';

	// Grafana dashboards, served same-origin under /monitoring (Caddy → pacod-grafana).
	// `kiosk` hides Grafana's own chrome for a clean embed; `theme` follows the app
	// theme; `refresh` auto-reloads panels. uids come from monitoring/grafana/dashboards/*.json;
	// the `pacod-gw-*` ones are the LLM gateway's (Grafana folder "pacod-gw"), which hold the
	// LLM token/cost/latency panels ("Usage" covers requests and turns only).
	const DASHBOARDS = [
		{
			id: 'app',
			label: m.metrics_tab_application(),
			icon: Activity,
			path: '/monitoring/d/pacod-app/pacod-application'
		},
		{
			id: 'infra',
			label: m.metrics_tab_infrastructure(),
			icon: Server,
			path: '/monitoring/d/pacod-infra/pacod-infrastructure'
		},
		{
			id: 'web',
			label: m.metrics_tab_web(),
			icon: Globe,
			path: '/monitoring/d/pacod-web/pacod-web-traffic'
		},
		{
			id: 'crawlers',
			label: m.metrics_tab_crawlers(),
			icon: Bot,
			path: '/monitoring/d/pacod-crawlers/pacod-crawler-traffic'
		},
		{
			id: 'usage',
			label: m.metrics_tab_usage(),
			icon: ChartColumn,
			path: '/monitoring/d/pacod-usage/pacod-usage'
		},
		{
			id: 'gateway',
			label: m.metrics_tab_gateway(),
			icon: Waypoints,
			path: '/monitoring/d/pacod-gw-overview'
		},
		{
			id: 'gateway-providers',
			label: m.metrics_tab_gateway_providers(),
			icon: Boxes,
			path: '/monitoring/d/pacod-gw-providers'
		}
	] as const;

	type DashboardId = (typeof DASHBOARDS)[number]['id'];
	let activeTab = $state<DashboardId>('app');

	function src(path: string): string {
		return `${path}?kiosk&refresh=30s&theme=${theme.current === 'dark' ? 'dark' : 'light'}`;
	}
</script>

<svelte:head><title>{m.metrics_title()}</title></svelte:head>

<!-- Fill the entire main area (undo the responsive padding from AppShell) -->
<div class="-m-4 flex h-[calc(100dvh-5.5rem)] flex-col sm:-m-6">
	<!-- Chrome bar: tab switcher + open-in-Grafana -->
	<div
		class="flex shrink-0 items-center justify-between border-b px-4 py-1.5"
		style="background: var(--color-surface-2); border-color: var(--color-border);"
	>
		<div class="flex min-w-0 items-center gap-1 overflow-x-auto">
			{#each DASHBOARDS as d}
				{@const Icon = d.icon}
				<button
					onclick={() => (activeTab = d.id)}
					class="flex shrink-0 items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium whitespace-nowrap transition-colors"
					style={activeTab === d.id
						? 'background: var(--color-surface-3); color: var(--color-text);'
						: 'color: var(--color-text-muted);'}
				>
					<Icon size={13} />
					{d.label}
				</button>
			{/each}
		</div>

		<a
			href="/monitoring"
			target="_blank"
			rel="noopener noreferrer"
			class="ml-3 inline-flex shrink-0 items-center gap-1 text-xs whitespace-nowrap text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-text)]"
		>
			{m.metrics_open_in_grafana()}
			<ExternalLink size={11} />
		</a>
	</div>

	<!-- Dashboard iframes (kept in DOM so switching tabs doesn't reload them) -->
	{#each DASHBOARDS as d}
		<div class="flex-1" class:hidden={activeTab !== d.id}>
			<iframe title={d.label} src={src(d.path)} class="h-full w-full border-0"></iframe>
		</div>
	{/each}
</div>
