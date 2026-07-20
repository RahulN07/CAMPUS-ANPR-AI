// DRF's PageNumberPagination wraps list responses as
// { count, next, previous, results }. Some custom views (dashboard, anpr)
// return plain arrays/objects instead. This normalizes both shapes.
export function unwrapList(data) {
  if (Array.isArray(data)) return { results: data, count: data.length };
  if (data && Array.isArray(data.results)) {
    return { results: data.results, count: data.count ?? data.results.length };
  }
  return { results: [], count: 0 };
}
