// The entry point: the one script the page loads.
//
// Everything else is an ES module and says what it needs at the top of its own
// file, so the browser resolves the graph and load order stops being something
// the HTML has to get right. Only the modules that do something on their own —
// the fetch wrapper that stamps a coder onto every API call, and the two
// document-level click handlers — have to be imported for their side effects.
import './00-coder.js';
import './10-state.js';
import './80-persist.js';
import { init } from './20-init.js';

init();
