import { type ClassValue, clsx } from 'clsx';
import { twMerge } from 'tailwind-merge';

/** Combine and dedupe Tailwind classes. Standard shadcn helper. */
export function cn(...inputs: ClassValue[]): string {
	return twMerge(clsx(inputs));
}
