<script setup lang="ts">
import HealthStatus from './components/HealthStatus.vue'
import { useHealth } from './composables/useHealth'
const { state, refresh } = useHealth()
</script>

<template>
  <a class="skip-link" href="#main">跳至主内容</a>
  <header class="app-header">
    <a class="brand" href="/" aria-label="EasyTravel 首页"><span class="brand-mark" aria-hidden="true">E<span>↗</span></span>EasyTravel</a>
    <nav aria-label="主导航"><a href="#health-heading" aria-current="page">服务状态</a></nav>
  </header>
  <main id="main" class="workspace">
    <div class="intro"><p class="eyebrow">EASYTRAVEL / 应用基础</p><h1>从连接开始。</h1><p class="intro-copy">查看后端服务状态，为接下来的旅程做好准备。</p></div>
    <HealthStatus :state="state" @refresh="refresh" />
    <aside class="stage-note"><span class="note-index" aria-hidden="true">01 /</span><div><h2>旅行规划，即将启程</h2><p>当前提供应用基础与服务状态检查。旅行规划交互将在后续阶段开放。</p></div></aside>
  </main>
  <footer class="app-footer"><span>EasyTravel</span><span>让每一步，都有据可循。</span></footer>
</template>
