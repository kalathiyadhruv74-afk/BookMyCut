function initMouseTracking() {
    const bg = document.querySelector('.mouse-follower-bg');
    if (!bg) return;

    // Throttle the mousemove event for performance
    let ticking = false;

    window.addEventListener('mousemove', (e) => {
        if (!ticking) {
            window.requestAnimationFrame(() => {
                const x = (e.clientX / window.innerWidth) * 100;
                const y = (e.clientY / window.innerHeight) * 100;

                bg.style.setProperty('--mouse-x', `${x}%`);
                bg.style.setProperty('--mouse-y', `${y}%`);

                ticking = false;
            });
            ticking = true;
        }
    });
}
