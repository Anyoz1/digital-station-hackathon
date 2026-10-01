// Integration fixture, not another product frontend. Replace BACKEND_URL on friend's PC.
import {defineConfig} from 'vite';
export default defineConfig({
  server:{host:'0.0.0.0',port:5173,strictPort:true,proxy:{
    '/api':{target:process.env.BACKEND_URL||'http://127.0.0.1:8000',changeOrigin:true,timeout:0,proxyTimeout:0},
    '/health':{target:process.env.BACKEND_URL||'http://127.0.0.1:8000',changeOrigin:true},
    '/tech':{target:process.env.BACKEND_URL||'http://127.0.0.1:8000',changeOrigin:true},
  }},
});
