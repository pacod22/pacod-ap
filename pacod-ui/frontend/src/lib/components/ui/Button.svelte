<script lang="ts" module>
	import { tv, type VariantProps } from 'tailwind-variants';

	export const buttonVariants = tv({
		base: 'inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-full font-display text-sm font-semibold tracking-[-0.01em] transition-all duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-brand)] focus-visible:ring-offset-2 active:translate-y-px disabled:pointer-events-none disabled:opacity-40',
		variants: {
			variant: {
				primary:
					'bg-[var(--color-brand)] text-[var(--color-brand-fg)] shadow-sm hover:bg-[var(--color-brand-muted)]',
				secondary:
					'border border-[var(--color-border-strong)] bg-transparent text-[var(--color-text)] hover:border-[var(--color-text)]',
				ghost:
					'text-[var(--color-text-muted)] hover:bg-[var(--color-surface-3)] hover:text-[var(--color-text)]',
				danger: 'bg-[var(--color-danger)] text-white shadow-sm hover:opacity-90',
				'danger-ghost':
					'text-[var(--color-danger)] hover:bg-[var(--color-danger-soft)] hover:text-[var(--color-danger)]'
			},
			size: {
				xs: 'h-7 px-3 text-xs',
				sm: 'h-8 px-4 text-xs',
				md: 'h-10 px-5',
				lg: 'h-11 px-7 text-base',
				icon: 'h-9 w-9'
			}
		},
		defaultVariants: { variant: 'primary', size: 'md' }
	});

	export type ButtonProps = VariantProps<typeof buttonVariants>;
</script>

<script lang="ts">
	import type { Snippet } from 'svelte';
	import type { HTMLButtonAttributes } from 'svelte/elements';
	import { cn } from '$lib/utils/cn';

	interface Props extends HTMLButtonAttributes, ButtonProps {
		children: Snippet;
		class?: string;
	}

	let {
		variant = 'primary',
		size = 'md',
		class: className = '',
		children,
		...rest
	}: Props = $props();
</script>

<button class={cn(buttonVariants({ variant, size }), className)} {...rest}>
	{@render children()}
</button>
