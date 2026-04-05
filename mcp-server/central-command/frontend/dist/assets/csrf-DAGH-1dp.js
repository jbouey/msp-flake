function e(){const n=document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/);return n?decodeURIComponent(n[1]):null}function o(){const n=e();return n?{"X-CSRF-Token":n}:{}}export{o as c};
