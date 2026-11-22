export function queueMaterialStorage<T>(store: object, key: string, task: () => Promise<T>): Promise<T>
