// The entry point: the one script the page loads.
//
// Every other file is an ES module that says at the top what it needs, so the
// browser resolves the graph from here and load order is not something the HTML
// has to get right. The three imports below are the modules that do something on
// their own rather than being called: the fetch wrapper that stamps the active
// coder onto every /api/ call, and the two document-level click handlers.
import './coder.js';
import './state.js';
import './persist.js';
import { init } from './boot.js';

init();
