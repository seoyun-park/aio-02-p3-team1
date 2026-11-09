export async function checkHealth(fetcher: (url: string) => Promise<Response> = fetch): Promise<boolean> {
  try {
    const response = await fetcher('/api/health');
    if (!response.ok) return false;
    const data: unknown = await response.json();
    return typeof data === 'object' && data !== null && 'status' in data && data.status === 'ok';
  } catch {
    return false;
  }
}
