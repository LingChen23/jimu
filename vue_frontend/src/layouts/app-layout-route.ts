export function isPresentationRoute(path: string): boolean {
  return path === '/realtime'
}

export function isWorkspaceRoute(path: string): boolean {
  return path === '/history'
    || path === '/devices'
    || path.startsWith('/results/')
}
