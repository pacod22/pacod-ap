<script lang="ts">
	import type { Snippet } from 'svelte';
	import AppShell from '$lib/components/layout/AppShell.svelte';
	import type { MyProfile } from '$lib/features/profiles/queries';

	let {
		data,
		children
	}: {
		data: { user: NonNullable<App.Locals['user']>; myProfiles: MyProfile[] };
		children: Snippet;
	} = $props();
</script>

<svelte:head>
	<!-- The whole admin console sits behind auth: a crawler only ever sees the
	     login redirect, and any URL that leaked would be a dead end in results.
	     robots.txt disallows these prefixes too — this is the belt to that brace. -->
	<meta name="robots" content="noindex, nofollow" />
</svelte:head>

<AppShell user={data.user} myProfiles={data.myProfiles}>
	{@render children()}
</AppShell>
