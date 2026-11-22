/** Prioritize exact matches within the returned page; preserve server order on ties. */
export function rankNetworkResults<T extends { name: string; aliases: string[] }>(rows: readonly T[], query: string): T[] {
  const needle = query.trim().toLocaleLowerCase()
  if (!needle) return [...rows]
  const rank = (row: T) => {
    const name = row.name.toLocaleLowerCase()
    if (name === needle) return 0
    if (row.aliases.some(alias => alias.toLocaleLowerCase() === needle)) return 1
    return name.startsWith(needle) ? 2 : 3
  }
  return [...rows].sort((a, b) => rank(a) - rank(b))
}
