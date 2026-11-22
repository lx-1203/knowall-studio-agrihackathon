// Queue a whole session's write drain, so a reopened reader sees its final saved position.
const tails = new WeakMap()
export function queueMaterialStorage(store, key, task) {
  // Bridge facades share the outer session drain while their inner storage IO
  // keeps a separate owner, avoiding a nested-queue deadlock.
  const owner = store.queueOwner ?? store
  let keys = tails.get(owner)
  if (!keys) { keys = new Map(); tails.set(owner, keys) }
  const operation = (keys.get(key) ?? Promise.resolve()).catch(() => {}).then(task)
  keys.set(key, operation)
  const clean = () => { if (keys.get(key) === operation) keys.delete(key) }
  void operation.then(clean, clean)
  return operation
}
