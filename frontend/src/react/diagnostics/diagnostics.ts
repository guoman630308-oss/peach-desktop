import { apiGet } from '../../api';
import { queryClient } from '../query';

export interface DiagnosticCheck {
  label: string; status: string; reason: string; action: string; target: string | null;
  details: Record<string, unknown>;
}
export interface LibraryItem {
  id: number; code: string | null; name: string; title: string; location: string; path: string;
  duration: number | null; size: number | null;
}
export interface LibraryGroup {
  label: string; status: string; count: number | null; items: LibraryItem[];
  truncated: boolean;
}
export interface DiagnosticReport {
  version: string; status: string; checks: Record<string, DiagnosticCheck>;
  library: { groups: Record<string, LibraryGroup>; issue_at: string | null };
  sources: Array<{ source: string; label: string; status: string; reason: string;
    last_success_at: string | null; cooldown_until: string | null;
    last_check_at?: string | null; elapsed_s?: number | null }>;
}
export const DIAGNOSTICS_KEY = ['diagnostics'] as const;
export const fetchDiagnostics = (signal?: AbortSignal) => apiGet<DiagnosticReport>('/api/diagnostics', signal);
export async function prefetchDiagnostics(signal: AbortSignal): Promise<void> {
  await queryClient.fetchQuery({ queryKey: DIAGNOSTICS_KEY, queryFn: () => fetchDiagnostics(signal), staleTime: 10_000 });
}
