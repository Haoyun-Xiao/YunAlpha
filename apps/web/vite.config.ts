import { defineConfig, type ProxyOptions } from 'vite';

const proxy: Record<string, string | ProxyOptions> = {
  '/api': `http://127.0.0.1:${process.env.RESEARCH_API_PORT || '19101'}`,
};
const ports: Record<string, number> = JSON.parse(process.env.YUNALPHA_PROXY_PORTS || '{}');
for (const [prefix, port] of Object.entries(ports)) {
  proxy[prefix] = {
    target: `http://127.0.0.1:${port}`,
    rewrite: path => path.slice(prefix.length) || '/',
  };
}

export default defineConfig(({mode}) => ({base: mode === 'showcase' ? '/YunAlpha/' : '/', server:{host:'127.0.0.1',strictPort:true,proxy}}));
