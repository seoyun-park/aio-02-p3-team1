type CategorizedEvent = { category: string | null };

export function listEventCategories(
  events: readonly CategorizedEvent[],
): string[] {
  const categories = events
    .map((event) => event.category?.trim())
    .filter((category): category is string => Boolean(category));
  return [...new Set(categories)].sort((left, right) =>
    left.localeCompare(right, "ko"),
  );
}

export function filterEventsByCategory<T extends CategorizedEvent>(
  events: readonly T[],
  category: string | null,
): T[] {
  if (!category) return [...events];
  return events.filter((event) => event.category?.trim() === category);
}
