export const basePath = import.meta.env.BASE_URL;
export const newsUrl = import.meta.env.MODE === 'showcase' ? '/YunAlpha/news/' : (import.meta.env.VITE_NEWS_URL || 'http://127.0.0.1:19281/');
export const publicShowcase = import.meta.env.MODE === 'showcase';

export function appPath(path: string) {
  return basePath + path.replace(/^\//, '');
}
