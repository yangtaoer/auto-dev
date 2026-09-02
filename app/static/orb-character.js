/* AutoDev Orb — WebGL volume, driven by the original spring/eye/trick engine. */
(function (global) {
  'use strict';

  const BRAND_ORANGE = '#f0522d';
  const EYE_INK = '#171813';
  const STATE_PROFILES = {
    idle:      {energy: .12, attention: .24, success: 0, error: 0, sleep: 0},
    curious:   {energy: .20, attention: .72, success: 0, error: 0, sleep: 0},
    listening: {energy: .18, attention: 1.00, success: 0, error: 0, sleep: 0},
    thinking:  {energy: .36, attention: .82, success: 0, error: 0, sleep: 0},
    working:   {energy: .88, attention: .56, success: 0, error: 0, sleep: 0},
    success:   {energy: .58, attention: .75, success: 1, error: 0, sleep: 0},
    error:     {energy: .08, attention: .32, success: 0, error: 1, sleep: 0},
    sleeping:  {energy: 0, attention: 0, success: 0, error: 0, sleep: 1},
    happy:     {energy: .62, attention: .64, success: .35, error: 0, sleep: 0},
    playful:   {energy: .82, attention: .78, success: .20, error: 0, sleep: 0},
    excited:   {energy: 1.00, attention: .82, success: .42, error: 0, sleep: 0},
    laughing:  {energy: .92, attention: .58, success: .46, error: 0, sleep: 0},
    proud:     {energy: .30, attention: .48, success: .30, error: 0, sleep: 0},
    shy:       {energy: .16, attention: .32, success: 0, error: 0, sleep: 0},
    searching: {energy: .72, attention: 1.00, success: 0, error: 0, sleep: 0},
    confused:  {energy: .34, attention: .88, success: 0, error: .28, sleep: 0},
    celebrate: {energy: 1.00, attention: .76, success: 1, error: 0, sleep: 0},
  };
  const DRIVER_STATES = {
    idle: 'idle', curious: 'curious', listening: 'listening', thinking: 'thinking',
    working: 'working', success: 'celebrate', error: 'confused', sleeping: 'sleeping',
  };

  const VERTEX_SHADER = `
    attribute vec2 a_position;
    void main() {
      gl_Position = vec4(a_position, 0.0, 1.0);
    }
  `;

  const FRAGMENT_SHADER = `
    precision highp float;

    uniform vec2 u_resolution;
    uniform vec2 u_pointer;
    uniform float u_time;
    uniform float u_age;
    uniform float u_blink;
    uniform float u_energy;
    uniform float u_attention;
    uniform float u_success;
    uniform float u_error;
    uniform float u_sleep;
    uniform float u_dark;
    uniform float u_turn;

    void main() {
      vec2 uv = gl_FragCoord.xy / u_resolution.xy * 2.0 - 1.0;
      uv.x *= u_resolution.x / u_resolution.y;

      float slow = u_time * (0.78 + u_energy * 1.35);
      float breathe = sin(slow * 1.18) * (0.003 + u_energy * 0.003);
      vec2 center = vec2(0.0, 0.0);
      vec2 local = uv - center;
      float radius = 0.825 + breathe;
      vec2 sphere = local / radius;
      float radial = length(sphere);
      float bodyAlpha = 1.0 - smoothstep(0.982, 1.018, radial);

      float shadowWide = mix(0.52, 0.59, u_dark);
      vec2 shadowPoint = vec2(uv.x, uv.y + 0.825);
      float shadow = exp(-pow(shadowPoint.x / shadowWide, 2.0) - pow(shadowPoint.y / 0.105, 2.0));
      float shadowAlpha = shadow * mix(0.19, 0.34, u_dark);
      vec3 shadowColor = mix(vec3(0.18, 0.09, 0.045), vec3(0.0), u_dark);

      if (bodyAlpha <= 0.001) {
        gl_FragColor = vec4(shadowColor * shadowAlpha, shadowAlpha);
        return;
      }

      float z = sqrt(max(0.0, 1.0 - dot(sphere, sphere)));
      vec3 normal = normalize(vec3(sphere.x, sphere.y, z));
      vec3 lightDirection = normalize(vec3(-0.58 + u_pointer.x * 0.08, 0.76 + u_pointer.y * 0.05, 0.92));
      float diffuse = max(dot(normal, lightDirection), 0.0);
      float halfLight = max(dot(normal, normalize(lightDirection + vec3(0.0, 0.0, 1.0))), 0.0);
      float specular = pow(halfLight, 34.0);
      float rim = pow(1.0 - z, 2.35);

      vec3 brand = vec3(0.941, 0.322, 0.176);
      vec3 vermilion = vec3(0.955, 0.235, 0.105);
      vec3 burnt = vec3(0.690, 0.115, 0.045);
      vec3 highlight = vec3(1.0, 0.815, 0.610);
      vec3 body = mix(burnt, brand, 0.48 + diffuse * 0.54);
      body = mix(body, vermilion, smoothstep(-0.45, 0.72, sphere.y) * 0.30);
      body += highlight * specular * 0.42;
      body += vermilion * rim * (0.10 + u_energy * 0.035);
      body *= 1.0 - smoothstep(0.08, 0.92, -sphere.y) * 0.15;
      body *= 1.0 - u_error * 0.22;

      float tide = sin((sphere.x * 1.35 - sphere.y * 0.85 + slow * 0.24 + u_turn * 0.34) * 3.14159265);
      body += vermilion * tide * (0.012 + u_energy * 0.010) * (1.0 - radial);

      float edgeGlow = rim * smoothstep(0.72, 1.0, radial) * 0.16;
      body += vermilion * edgeGlow;
      float solidAlpha = bodyAlpha * 0.985;
      vec3 premultiplied = body * solidAlpha + shadowColor * shadowAlpha * (1.0 - solidAlpha);
      float alpha = solidAlpha + shadowAlpha * (1.0 - solidAlpha);
      gl_FragColor = vec4(premultiplied, alpha);
    }
  `;

  function compileShader(gl, type, source) {
    const shader = gl.createShader(type);
    gl.shaderSource(shader, source);
    gl.compileShader(shader);
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
      const message = gl.getShaderInfoLog(shader) || 'unknown shader error';
      gl.deleteShader(shader);
      throw new Error(message);
    }
    return shader;
  }

  function createProgram(gl) {
    const program = gl.createProgram();
    const vertex = compileShader(gl, gl.VERTEX_SHADER, VERTEX_SHADER);
    const fragment = compileShader(gl, gl.FRAGMENT_SHADER, FRAGMENT_SHADER);
    gl.attachShader(program, vertex);
    gl.attachShader(program, fragment);
    gl.linkProgram(program);
    gl.deleteShader(vertex);
    gl.deleteShader(fragment);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
      const message = gl.getProgramInfoLog(program) || 'unknown program error';
      gl.deleteProgram(program);
      throw new Error(message);
    }
    return program;
  }

  class AutoDevOrb {
    constructor(root, options = {}) {
      if (!root) throw new Error('AutoDevOrb requires a root element');
      this.root = root;
      this.canvas = root.querySelector('.autodev-orb-canvas');
      this.fallbackSvg = root.querySelector('.autodev-orb-fallback');
      this.options = Object.assign({followPointer: true, environment: 'light', state: 'curious', mode: 'manual', ambient: true}, options);
      this.destroyed = false;
      this.manualPaused = false;
      this.frameRequest = 0;
      this.startedAt = performance.now();
      this.stateStartedAt = this.startedAt;
      this.lastFrameAt = this.startedAt;
      this.state = 'curious';
      this.pointer = {x: 0, y: 0, targetX: 0, targetY: 0};
      this.profile = Object.assign({}, STATE_PROFILES.curious);
      this.targetProfile = Object.assign({}, STATE_PROFILES.curious);
      this.driverTurn = 0;
      this.reduceMotionQuery = global.matchMedia('(prefers-reduced-motion: reduce)');
      this.reducedMotion = this.reduceMotionQuery.matches;

      this._onPointerMove = this._onPointerMove.bind(this);
      this._onVisibilityChange = this._onVisibilityChange.bind(this);
      this._onMotionChange = this._onMotionChange.bind(this);
      this._onContextLost = this._onContextLost.bind(this);
      this._resize = this._resize.bind(this);
      this._frame = this._frame.bind(this);

      this.webglReady = Boolean(this.canvas && this._initializeWebGL());
      this._initializeMotionDriver(!this.webglReady);
      this.setState(this.options.state, {immediate: true});
      this._bind();
      this._resize();
      this._schedule();
    }

    _initializeWebGL() {
      try {
        const gl = this.canvas.getContext('webgl', {
          alpha: true, antialias: true, depth: false, stencil: false,
          premultipliedAlpha: true, preserveDrawingBuffer: false,
          powerPreference: 'low-power',
        });
        if (!gl) return false;
        const program = createProgram(gl);
        const buffer = gl.createBuffer();
        gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
        gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, -1, 1, 1, -1, 1, 1]), gl.STATIC_DRAW);
        gl.useProgram(program);
        const position = gl.getAttribLocation(program, 'a_position');
        gl.enableVertexAttribArray(position);
        gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0);
        this.gl = gl;
        this.program = program;
        this.buffer = buffer;
        this.uniforms = {};
        ['resolution', 'pointer', 'time', 'age', 'blink', 'energy', 'attention', 'success', 'error', 'sleep', 'dark', 'turn'].forEach(name => {
          this.uniforms[name] = gl.getUniformLocation(program, `u_${name}`);
        });
        gl.clearColor(0, 0, 0, 0);
        this.root.classList.add('is-webgl');
        return true;
      } catch (error) {
        this.gl = null;
        return false;
      }
    }

    _initializeMotionDriver(asFallback) {
      this.root.classList.toggle('is-fallback', asFallback);
      if (!this.fallbackSvg || typeof global.GrokCharacter !== 'function') return;
      this.motionDriver = new global.GrokCharacter(this.fallbackSvg, {
        mode: this.options.mode,
        state: DRIVER_STATES[this.options.state] || 'curious',
        shape: 'blob',
        color: 'black',
        scheme: this.options.environment === 'dark' ? 'dark' : 'light',
        inkFlat: BRAND_ORANGE,
        eyeColor: EYE_INK,
        loginWrap: true,
        followPointer: this.options.followPointer,
        sizePx: this.options.sizePx || 112,
      });
      this.motionDriver.moodN = 1;
      this.motionDriver.setPaused(this.reducedMotion);
      this.fallbackCharacter = this.motionDriver;
      if (!asFallback && this.motionDriver.body) this.motionDriver.body.classList.add('orb-driver-body');
    }

    _bind() {
      if (this.options.followPointer) global.addEventListener('pointermove', this._onPointerMove, {passive: true});
      if (this.canvas) this.canvas.addEventListener('webglcontextlost', this._onContextLost);
      document.addEventListener('visibilitychange', this._onVisibilityChange);
      this.reduceMotionQuery.addEventListener('change', this._onMotionChange);
      if ('ResizeObserver' in global) {
        this.resizeObserver = new ResizeObserver(this._resize);
        this.resizeObserver.observe(this.root);
      } else {
        global.addEventListener('resize', this._resize, {passive: true});
      }
    }

    _onPointerMove(event) {
      const rect = this.root.getBoundingClientRect();
      if (!rect.width || !rect.height) return;
      this.pointer.targetX = Math.max(-1, Math.min(1, (event.clientX - (rect.left + rect.width / 2)) / (rect.width * 1.7)));
      this.pointer.targetY = Math.max(-1, Math.min(1, ((rect.top + rect.height / 2) - event.clientY) / (rect.height * 1.7)));
    }

    _onVisibilityChange() {
      if (this.fallbackCharacter) this.fallbackCharacter.setPaused(document.hidden || this.manualPaused || this.reducedMotion);
      if (!document.hidden) {
        this.lastFrameAt = performance.now();
        this._schedule();
      }
    }

    _onMotionChange(event) {
      this.reducedMotion = event.matches;
      if (this.fallbackCharacter) this.fallbackCharacter.setPaused(this.manualPaused || this.reducedMotion);
      this._schedule(this.reducedMotion);
    }

    _onContextLost(event) {
      event.preventDefault();
      this.webglReady = false;
      this.gl = null;
      this.root.classList.remove('is-webgl');
      this.root.classList.add('is-fallback');
      if (this.motionDriver?.body) this.motionDriver.body.classList.remove('orb-driver-body');
      if (this.canvas) this.canvas.style.transform = '';
    }

    _resize() {
      if (!this.gl || !this.canvas) return;
      const rect = this.root.getBoundingClientRect();
      if (!rect.width || !rect.height) return;
      const dpr = Math.min(global.devicePixelRatio || 1, 1.5);
      const width = Math.max(1, Math.round(rect.width * dpr));
      const height = Math.max(1, Math.round(rect.height * dpr));
      if (this.canvas.width !== width || this.canvas.height !== height) {
        this.canvas.width = width;
        this.canvas.height = height;
        this.gl.viewport(0, 0, width, height);
      }
      this._syncDriverMotion();
      this._render(performance.now());
    }

    _schedule(forceStatic = false) {
      if (!this.gl || this.destroyed) return;
      if (forceStatic || this.reducedMotion || this.manualPaused || document.hidden) {
        if (this.frameRequest) cancelAnimationFrame(this.frameRequest);
        this.frameRequest = 0;
        this._syncDriverMotion();
        this._render(performance.now());
        return;
      }
      if (!this.frameRequest) this.frameRequest = requestAnimationFrame(this._frame);
    }

    _frame(now) {
      this.frameRequest = 0;
      if (this.destroyed || this.manualPaused || document.hidden || this.reducedMotion) return;
      const delta = Math.min(50, Math.max(0, now - this.lastFrameAt));
      const profileEase = 1 - Math.exp(-delta / 280);
      const pointerEase = 1 - Math.exp(-delta / 150);
      Object.keys(this.profile).forEach(key => {
        this.profile[key] += (this.targetProfile[key] - this.profile[key]) * profileEase;
      });
      this.pointer.x += (this.pointer.targetX - this.pointer.x) * pointerEase;
      this.pointer.y += (this.pointer.targetY - this.pointer.y) * pointerEase;
      this._syncDriverMotion();
      this.lastFrameAt = now;
      this._render(now);
      this._schedule();
    }

    _syncDriverMotion() {
      if (!this.motionDriver) return;
      const motionState = this.motionDriver.state || DRIVER_STATES[this.state] || this.state;
      this.root.dataset.motionState = motionState;
      this.root.dataset.eye = String(this.motionDriver.eyeTo ?? '');
      const motionProfile = STATE_PROFILES[motionState] || STATE_PROFILES[this.state] || STATE_PROFILES.curious;
      this.targetProfile = Object.assign({}, motionProfile);
      if (!this.webglReady || !this.canvas || !this.motionDriver.group) return;
      const transform = this.motionDriver.group.getAttribute('transform') || '';
      const match = transform.match(/translate\(([-\d.]+)\s+([-\d.]+)\)\s+rotate\(([-\d.]+)\)\s+scale\(([-\d.]+)\s+([-\d.]+)\)/);
      if (!match) return;
      const center = global.GROK_GEO?.Re || 114.2705;
      const viewWidth = global.GROK_TABLES?.VIEW?.width || 259;
      const pixelScale = this.root.getBoundingClientRect().width / viewWidth;
      const poseScale = this.motionDriver.pose?.scale || 1;
      const dx = (Number(match[1]) - center) * pixelScale * poseScale;
      const dy = (Number(match[2]) - center) * pixelScale * poseScale;
      const rotation = Number(match[3]);
      const sx = Number(match[4]) * poseScale;
      const sy = Number(match[5]) * poseScale;
      const surfaceTurn = Number(this.motionDriver.extras?.turn);
      this.driverTurn = Number.isFinite(surfaceTurn) ? surfaceTurn : rotation * Math.PI / 180;
      this.canvas.style.transform = `translate3d(${dx.toFixed(3)}px, ${dy.toFixed(3)}px, 0) rotate(${rotation.toFixed(3)}deg) scale(${sx.toFixed(4)}, ${sy.toFixed(4)})`;
    }

    _render(now) {
      if (!this.gl || !this.canvas.width || !this.canvas.height) return;
      const gl = this.gl;
      gl.useProgram(this.program);
      gl.clear(gl.COLOR_BUFFER_BIT);
      gl.uniform2f(this.uniforms.resolution, this.canvas.width, this.canvas.height);
      gl.uniform2f(this.uniforms.pointer, this.pointer.x, this.pointer.y);
      gl.uniform1f(this.uniforms.time, this.reducedMotion ? 0.6 : (now - this.startedAt) / 1000);
      gl.uniform1f(this.uniforms.age, (now - this.stateStartedAt) / 1000);
      gl.uniform1f(this.uniforms.blink, this.motionDriver?.blink?.x ?? 1);
      gl.uniform1f(this.uniforms.energy, this.profile.energy);
      gl.uniform1f(this.uniforms.attention, this.profile.attention);
      gl.uniform1f(this.uniforms.success, this.profile.success);
      gl.uniform1f(this.uniforms.error, this.profile.error);
      gl.uniform1f(this.uniforms.sleep, this.profile.sleep);
      gl.uniform1f(this.uniforms.dark, this.options.environment === 'dark' ? 1 : 0);
      gl.uniform1f(this.uniforms.turn, this.driverTurn);
      gl.drawArrays(gl.TRIANGLES, 0, 6);
    }

    setState(name, {immediate = false} = {}) {
      const state = STATE_PROFILES[name] ? name : 'curious';
      if (this.state === state && !immediate) return;
      this.state = state;
      this.stateStartedAt = performance.now();
      this.targetProfile = Object.assign({}, STATE_PROFILES[state]);
      if (immediate || this.reducedMotion) this.profile = Object.assign({}, this.targetProfile);
      if (this.motionDriver) {
        const useOnboarding = state === 'curious' && (this.options.ambient || this.options.mode === 'onboarding');
        const driverMode = useOnboarding ? 'onboarding' : 'manual';
        if (this.motionDriver.mode !== driverMode) {
          this.motionDriver.setMode(driverMode);
          if (useOnboarding) this.motionDriver.moodN = 1;
        }
        this.motionDriver.setState(DRIVER_STATES[state] || 'curious', {resetEyes: immediate});
        if (state === 'success' && !this.reducedMotion) this.motionDriver.burstOnce();
      }
      this.root.dataset.state = state;
      this._schedule();
    }

    setPaused(paused) {
      this.manualPaused = Boolean(paused);
      if (this.fallbackCharacter) this.fallbackCharacter.setPaused(this.manualPaused || this.reducedMotion);
      this._schedule(this.manualPaused);
    }

    spinOnce(turns = 1) { this.motionDriver?.spinOnce(turns); }
    bounceOnce() { this.motionDriver?.bounceOnce(); }
    burstOnce() { this.motionDriver?.burstOnce(); }

    destroy() {
      if (this.destroyed) return;
      this.destroyed = true;
      if (this.frameRequest) cancelAnimationFrame(this.frameRequest);
      global.removeEventListener('pointermove', this._onPointerMove);
      if (this.canvas) this.canvas.removeEventListener('webglcontextlost', this._onContextLost);
      global.removeEventListener('resize', this._resize);
      document.removeEventListener('visibilitychange', this._onVisibilityChange);
      this.reduceMotionQuery.removeEventListener('change', this._onMotionChange);
      if (this.resizeObserver) this.resizeObserver.disconnect();
      if (this.fallbackCharacter) this.fallbackCharacter.destroy();
      if (this.gl) {
        this.gl.deleteBuffer(this.buffer);
        this.gl.deleteProgram(this.program);
      }
    }
  }

  AutoDevOrb.BRAND_ORANGE = BRAND_ORANGE;
  global.AutoDevOrb = AutoDevOrb;
})(window);
