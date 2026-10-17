<script lang="ts">
	import { Sparkles } from 'lucide-svelte';
	import { m } from '$lib/paraglide/messages';

	interface Props {
		title: string;
		description: string;
		// All lucide-svelte icons share this component shape.
		icon?: typeof Sparkles;
	}

	let { title, description, icon }: Props = $props();
	const Icon = $derived(icon ?? Sparkles);
</script>

<div
	class="flex h-full flex-col items-center justify-center gap-3 rounded-xl border border-dashed px-6 py-10 text-center"
	style="border-color: var(--color-border); background: var(--color-surface-2);"
>
	<div
		class="flex h-11 w-11 items-center justify-center rounded-xl"
		style="background: var(--color-brand-soft);"
	>
		<Icon size={20} style="color: var(--color-brand);" />
	</div>
	<div>
		<div class="flex items-center justify-center gap-2">
			<span class="text-sm font-semibold">{title}</span>
			<span
				class="rounded-full px-2 py-0.5 text-[10px] font-semibold tracking-wide uppercase"
				style="background: var(--color-gold-soft); color: var(--color-gold);"
			>
				{m.common_coming_soon()}
			</span>
		</div>
		<p class="mx-auto mt-1 max-w-xs text-xs text-[var(--color-text-muted)]">{description}</p>
	</div>
</div>
