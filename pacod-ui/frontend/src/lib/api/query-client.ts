import { QueryClient } from '@tanstack/svelte-query';
import { browser } from '$app/environment';

/**
 * Single QueryClient per app instance. SSR-friendly: the query cache
 * is hydrated from the server load function via the +layout.ts dehydrate
 * pattern (set up in routes/+layout.ts).
 */
export function createQueryClient(): QueryClient {
	return new QueryClient({
		defaultOptions: {
			queries: {
				// Don't refetch on every focus during dev — annoying.
				refetchOnWindowFocus: import.meta.env.PROD,
				// Stale-while-revalidate sweet spot for an admin app.
				staleTime: 30_000,
				retry: (failureCount, error) => {
					// Don't retry 4xx; those are deterministic.
					if (error && typeof error === 'object' && 'status' in error) {
						const status = (error as { status: number }).status;
						if (status >= 400 && status < 500) return false;
					}
					return failureCount < 2;
				},
				// Disable network queries during SSR; load functions handle SSR data.
				enabled: browser
			},
			mutations: {
				retry: false
			}
		}
	});
}
