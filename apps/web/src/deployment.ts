export const basePath = import.meta.env.BASE_URL;
export const publicShowcase = import.meta.env.MODE === 'showcase';

export function appPath(path: string) {
  return basePath + path.replace(/^\//, '');
}
