import './app.css';
import './lib/design/pro.css';
import { mount } from 'svelte';
import App from './App.svelte';
import { initAppearance } from './lib/appearance.svelte';

initAppearance();

const app = mount(App, { target: document.getElementById('app')! });

export default app;
