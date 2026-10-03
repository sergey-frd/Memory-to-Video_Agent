const fs=require('fs'),vm=require('vm'),assert=require('assert');
const lines=fs.readFileSync('scripts/full_master_native.jsx','utf8').split('\n').filter(l=>l.startsWith('function normalizedPath')||l.startsWith('function same'));
const ctx={};vm.createContext(ctx);vm.runInContext(lines.join('\n'),ctx);
assert(ctx.same('<LOCAL_PATH>','<LOCAL_PATH>'));
assert(ctx.same('<LOCAL_PATH>','<LOCAL_PATH>'));
assert(!ctx.same('<LOCAL_PATH>','<LOCAL_PATH>'));
assert(ctx.same('\\\\server\\share\\file','//server/share/file'));
console.log('4 path comparisons PASS');
