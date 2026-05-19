import * as THREE from 'three';

(() => {
    const container = document.getElementById('hero3d');
    if (!container) return;

    if (typeof WebGLRenderingContext === 'undefined') {
        container.classList.add('hero3d-unavailable');
        return;
    }

    const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    const getTheme = () => document.documentElement.getAttribute('data-theme') || 'light';

    const palette = (theme) => theme === 'dark'
        ? {
            primary: 0x3dd5c3,
            accent: 0xfb923c,
            neutral: 0xa6b5c6,
            glow: 0x67e8d5,
            ambient: 0x0b1a26,
            ambientIntensity: 0.45,
            keyIntensity: 1.25,
            particleAlpha: 0.55,
            barEmissive: 0.28,
            nodeEmissive: 0.7,
        }
        : {
            primary: 0x0f766e,
            accent: 0xf97316,
            neutral: 0x5d6979,
            glow: 0x5fd7c9,
            ambient: 0xeaf6f4,
            ambientIntensity: 0.65,
            keyIntensity: 1.35,
            particleAlpha: 0.5,
            barEmissive: 0.12,
            nodeEmissive: 0.4,
        };

    let colors = palette(getTheme());

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(38, 1, 0.1, 100);
    camera.position.set(0, 1.4, 7.4);
    camera.lookAt(0, 0, 0);

    const renderer = new THREE.WebGLRenderer({
        antialias: true,
        alpha: true,
        powerPreference: 'high-performance',
    });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setClearColor(0x000000, 0);
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.05;
    container.appendChild(renderer.domElement);
    renderer.domElement.classList.add('hero3d-canvas');

    const ambient = new THREE.AmbientLight(colors.ambient, colors.ambientIntensity);
    scene.add(ambient);

    const keyLight = new THREE.DirectionalLight(0xffffff, colors.keyIntensity);
    keyLight.position.set(4, 7, 6);
    scene.add(keyLight);

    const fillPrimary = new THREE.PointLight(colors.primary, 1.6, 18);
    fillPrimary.position.set(-4, 2.5, 3);
    scene.add(fillPrimary);

    const fillAccent = new THREE.PointLight(colors.accent, 1.2, 18);
    fillAccent.position.set(3.6, -1.8, 3);
    scene.add(fillAccent);

    const root = new THREE.Group();
    scene.add(root);

    const coreGroup = new THREE.Group();
    root.add(coreGroup);

    const coreMat = new THREE.MeshPhysicalMaterial({
        color: colors.primary,
        metalness: 0.18,
        roughness: 0.08,
        transmission: 0.82,
        thickness: 0.5,
        ior: 1.45,
        clearcoat: 1,
        clearcoatRoughness: 0.06,
        envMapIntensity: 1.2,
        side: THREE.DoubleSide,
        transparent: true,
        opacity: 0.92,
    });
    const coreMesh = new THREE.Mesh(new THREE.IcosahedronGeometry(1.05, 0), coreMat);
    coreGroup.add(coreMesh);

    const glowMat = new THREE.MeshBasicMaterial({
        color: colors.glow,
        transparent: true,
        opacity: 0.7,
        depthWrite: false,
    });
    const glowMesh = new THREE.Mesh(new THREE.SphereGeometry(0.5, 32, 32), glowMat);
    coreGroup.add(glowMesh);

    const wireMat = new THREE.MeshBasicMaterial({
        color: colors.accent,
        wireframe: true,
        transparent: true,
        opacity: 0.48,
    });
    const wireMesh = new THREE.Mesh(new THREE.IcosahedronGeometry(1.45, 1), wireMat);
    coreGroup.add(wireMesh);

    const barsGroup = new THREE.Group();
    const bars = [];
    const barCount = 9;
    const barRadius = 2.55;
    for (let i = 0; i < barCount; i++) {
        const angle = (i / barCount) * Math.PI * 2;
        const baseHeight = 0.65 + Math.random() * 1.35;
        const isAccent = i % 2 === 1;
        const color = isAccent ? colors.accent : colors.primary;
        const mat = new THREE.MeshStandardMaterial({
            color,
            metalness: 0.55,
            roughness: 0.28,
            emissive: color,
            emissiveIntensity: colors.barEmissive,
        });
        const bar = new THREE.Mesh(new THREE.BoxGeometry(0.34, baseHeight, 0.34), mat);
        bar.position.set(Math.cos(angle) * barRadius, baseHeight / 2 - 1.45, Math.sin(angle) * barRadius);
        bar.userData = { baseHeight, baseY: baseHeight / 2 - 1.45, phase: Math.random() * Math.PI * 2, isAccent };
        bars.push(bar);
        barsGroup.add(bar);
    }
    root.add(barsGroup);

    function makeRing(radius, tube, color, opacity) {
        const geo = new THREE.TorusGeometry(radius, tube, 18, 140);
        const mat = new THREE.MeshBasicMaterial({ color, transparent: true, opacity });
        return new THREE.Mesh(geo, mat);
    }

    const ring1 = makeRing(1.95, 0.018, colors.primary, 0.72);
    ring1.rotation.x = Math.PI / 2.4;
    root.add(ring1);

    const ring2 = makeRing(2.35, 0.012, colors.accent, 0.55);
    ring2.rotation.x = -Math.PI / 3;
    ring2.rotation.z = Math.PI / 6;
    root.add(ring2);

    const ring3 = makeRing(1.6, 0.008, colors.accent, 0.45);
    ring3.rotation.x = Math.PI / 1.6;
    ring3.rotation.y = Math.PI / 4;
    root.add(ring3);

    const nodeCount = 26;
    const nodes = [];
    for (let i = 0; i < nodeCount; i++) {
        const r = 3.05 + Math.random() * 1.25;
        const theta = Math.random() * Math.PI * 2;
        const phi = Math.acos(2 * Math.random() - 1);
        const x = r * Math.sin(phi) * Math.cos(theta);
        const y = r * Math.cos(phi) * 0.55;
        const z = r * Math.sin(phi) * Math.sin(theta);
        const isAccent = Math.random() > 0.55;
        const color = isAccent ? colors.accent : colors.primary;
        const mat = new THREE.MeshStandardMaterial({
            color,
            emissive: color,
            emissiveIntensity: colors.nodeEmissive,
            metalness: 0.55,
            roughness: 0.35,
        });
        const node = new THREE.Mesh(new THREE.SphereGeometry(0.05 + Math.random() * 0.08, 14, 14), mat);
        node.position.set(x, y, z);
        node.userData = {
            base: new THREE.Vector3(x, y, z),
            phase: Math.random() * Math.PI * 2,
            speed: 0.55 + Math.random() * 0.7,
            amp: 0.07 + Math.random() * 0.15,
            isAccent,
        };
        nodes.push(node);
        root.add(node);
    }

    const lineCount = 10;
    const lineSegments = [];
    for (let i = 0; i < lineCount; i++) {
        const a = nodes[Math.floor(Math.random() * nodes.length)];
        const b = nodes[Math.floor(Math.random() * nodes.length)];
        if (a === b) continue;
        const geometry = new THREE.BufferGeometry().setFromPoints([a.position.clone(), b.position.clone()]);
        const material = new THREE.LineBasicMaterial({
            color: colors.neutral,
            transparent: true,
            opacity: 0.22,
        });
        const line = new THREE.Line(geometry, material);
        line.userData = { a, b };
        lineSegments.push(line);
        root.add(line);
    }

    const particleCount = 260;
    const particlePositions = new Float32Array(particleCount * 3);
    for (let i = 0; i < particleCount; i++) {
        const r = 4 + Math.random() * 4.5;
        const theta = Math.random() * Math.PI * 2;
        const phi = Math.acos(2 * Math.random() - 1);
        particlePositions[i * 3 + 0] = r * Math.sin(phi) * Math.cos(theta);
        particlePositions[i * 3 + 1] = r * Math.cos(phi) * 0.6;
        particlePositions[i * 3 + 2] = r * Math.sin(phi) * Math.sin(theta);
    }
    const particleGeo = new THREE.BufferGeometry();
    particleGeo.setAttribute('position', new THREE.BufferAttribute(particlePositions, 3));
    const particleMat = new THREE.PointsMaterial({
        size: 0.045,
        color: colors.neutral,
        transparent: true,
        opacity: colors.particleAlpha,
        depthWrite: false,
        blending: THREE.AdditiveBlending,
        sizeAttenuation: true,
    });
    const particles = new THREE.Points(particleGeo, particleMat);
    scene.add(particles);

    function resize() {
        const rect = container.getBoundingClientRect();
        const width = Math.max(rect.width, 1);
        const height = Math.max(rect.height, 320);
        renderer.setSize(width, height, false);
        camera.aspect = width / height;
        camera.updateProjectionMatrix();
    }
    resize();

    const ro = new ResizeObserver(resize);
    ro.observe(container);

    const mouse = { x: 0, y: 0, targetX: 0, targetY: 0 };
    function onPointerMove(e) {
        const rect = container.getBoundingClientRect();
        const x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
        const y = ((e.clientY - rect.top) / rect.height) * 2 - 1;
        mouse.targetX = Math.max(-1, Math.min(1, x));
        mouse.targetY = Math.max(-1, Math.min(1, y));
    }
    container.addEventListener('pointermove', onPointerMove);
    container.addEventListener('pointerleave', () => {
        mouse.targetX = 0;
        mouse.targetY = 0;
    });

    function applyPalette(next) {
        coreMat.color.set(next.primary);
        glowMat.color.set(next.glow);
        wireMat.color.set(next.accent);
        ambient.color.set(next.ambient);
        ambient.intensity = next.ambientIntensity;
        keyLight.intensity = next.keyIntensity;
        fillPrimary.color.set(next.primary);
        fillAccent.color.set(next.accent);
        ring1.material.color.set(next.primary);
        ring2.material.color.set(next.accent);
        ring3.material.color.set(next.accent);
        particleMat.color.set(next.neutral);
        particleMat.opacity = next.particleAlpha;
        bars.forEach((bar) => {
            const target = bar.userData.isAccent ? next.accent : next.primary;
            bar.material.color.set(target);
            bar.material.emissive.set(target);
            bar.material.emissiveIntensity = next.barEmissive;
        });
        nodes.forEach((node) => {
            const target = node.userData.isAccent ? next.accent : next.primary;
            node.material.color.set(target);
            node.material.emissive.set(target);
            node.material.emissiveIntensity = next.nodeEmissive;
        });
        lineSegments.forEach((line) => line.material.color.set(next.neutral));
        colors = next;
    }

    window.addEventListener('di:theme-change', (e) => {
        applyPalette(palette(e.detail?.theme || getTheme()));
    });

    let isVisible = true;
    if ('IntersectionObserver' in window) {
        const io = new IntersectionObserver((entries) => {
            entries.forEach((entry) => {
                isVisible = entry.isIntersecting;
            });
        }, { threshold: 0.01 });
        io.observe(container);
    }

    document.addEventListener('visibilitychange', () => {
        isVisible = !document.hidden && isVisible;
    });

    const clock = new THREE.Clock();
    let lastFrame = 0;
    const targetFps = 60;
    const targetFrameMs = 1000 / targetFps;

    function animate(now) {
        requestAnimationFrame(animate);
        if (!isVisible) return;
        if (now - lastFrame < targetFrameMs - 1) return;
        lastFrame = now;

        const t = clock.getElapsedTime();
        const dt = clock.getDelta();

        mouse.x += (mouse.targetX - mouse.x) * 0.05;
        mouse.y += (mouse.targetY - mouse.y) * 0.05;

        const rotationSpeed = prefersReducedMotion ? 0.04 : 0.16;
        root.rotation.y += dt * rotationSpeed;
        root.rotation.x = mouse.y * 0.16;
        root.position.y = Math.sin(t * 0.55) * 0.08;

        coreMesh.rotation.x += dt * 0.4;
        coreMesh.rotation.y += dt * 0.32;
        wireMesh.rotation.x -= dt * 0.22;
        wireMesh.rotation.y -= dt * 0.28;

        const pulse = 0.55 + Math.sin(t * 2.0) * 0.16;
        glowMesh.scale.setScalar(pulse);
        glowMat.opacity = 0.45 + Math.sin(t * 1.6) * 0.2;

        for (let i = 0; i < bars.length; i++) {
            const bar = bars[i];
            const { baseHeight, phase } = bar.userData;
            const wave = Math.sin(t * 1.5 + phase + i * 0.42);
            const factor = 1 + wave * 0.32;
            bar.scale.y = factor;
            bar.position.y = (baseHeight * factor) / 2 - 1.45;
        }

        ring1.rotation.z += dt * 0.18;
        ring2.rotation.y += dt * 0.22;
        ring3.rotation.x += dt * 0.28;

        for (let i = 0; i < nodes.length; i++) {
            const node = nodes[i];
            const { base, phase, speed, amp } = node.userData;
            const wx = Math.sin(t * speed + phase);
            const wy = Math.cos(t * speed * 0.9 + phase);
            node.position.set(
                base.x + wx * amp * 0.5,
                base.y + wy * amp,
                base.z + wx * amp * 0.4,
            );
        }

        for (let i = 0; i < lineSegments.length; i++) {
            const line = lineSegments[i];
            const { a, b } = line.userData;
            const positions = line.geometry.attributes.position.array;
            positions[0] = a.position.x;
            positions[1] = a.position.y;
            positions[2] = a.position.z;
            positions[3] = b.position.x;
            positions[4] = b.position.y;
            positions[5] = b.position.z;
            line.geometry.attributes.position.needsUpdate = true;
            line.material.opacity = 0.16 + (Math.sin(t * 0.8 + i) + 1) * 0.06;
        }

        particles.rotation.y += dt * 0.04;
        particles.rotation.x += dt * 0.015;

        camera.position.x = mouse.x * 0.6;
        camera.position.y = 1.4 - mouse.y * 0.25;
        camera.lookAt(0, 0, 0);

        renderer.render(scene, camera);
    }

    if (window.gsap) {
        root.scale.set(0.55, 0.55, 0.55);
        window.gsap.to(root.scale, { x: 1, y: 1, z: 1, duration: 1.2, ease: 'power3.out' });

        bars.forEach((bar, i) => {
            const targetY = bar.position.y;
            bar.position.y = targetY - 1.8;
            bar.material.transparent = true;
            bar.material.opacity = 0;
            window.gsap.to(bar.position, { y: targetY, duration: 0.95, delay: 0.35 + i * 0.05, ease: 'back.out(1.5)' });
            window.gsap.to(bar.material, { opacity: 1, duration: 0.6, delay: 0.35 + i * 0.05 });
        });

        nodes.forEach((node, i) => {
            node.material.transparent = true;
            const target = node.material.opacity;
            node.material.opacity = 0;
            window.gsap.to(node.material, { opacity: target, duration: 0.7, delay: 0.6 + i * 0.018 });
        });

        lineSegments.forEach((line, i) => {
            const target = line.material.opacity;
            line.material.opacity = 0;
            window.gsap.to(line.material, { opacity: target, duration: 0.8, delay: 0.9 + i * 0.04 });
        });
    }

    requestAnimationFrame(animate);

    const fallback = container.querySelector('.hero3d-fallback');
    if (fallback) {
        fallback.classList.add('hero3d-fallback-hidden');
    }
    container.classList.add('hero3d-ready');
})();
