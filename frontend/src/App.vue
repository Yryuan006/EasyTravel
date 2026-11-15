<script setup lang="ts">
import HealthStatus from './components/HealthStatus.vue'
import { useHealth } from './composables/useHealth'
import { computed, onUnmounted, ref } from 'vue'
import RequirementsForm from './components/RequirementsForm.vue'
import TripHistory from './components/TripHistory.vue'
import TripWorkspace from './components/TripWorkspace.vue'
import { useTrip } from './composables/useTrip'
import type { RequirementsInput } from './api/contracts'
const { state, refresh } = useHealth()
const trip = useTrip()
const token = ref('')
const credential = ref('')
const path = ref(location.pathname)
const identityEpoch = ref(0)
const created = ref(false)
const isNew = computed(() => path.value === '/' || path.value === '/trips/new')
const historyMatch = computed(() => /^\/trips\/([A-Za-z0-9_-]{1,80})\/history(?:\/([A-Za-z0-9_-]{1,80}))?$/.exec(path.value))
const tripId = computed(() => isNew.value ? undefined : historyMatch.value?.[1] ?? /^\/trips\/([^/]+)$/.exec(path.value)?.[1])
const localDevelopment = import.meta.env.DEV && ['127.0.0.1', 'localhost', '[::1]'].includes(location.hostname)
function navigate(url: string) { history.pushState(null, '', url); path.value = url }
function onPop() { trip.reset(); created.value = false; path.value = location.pathname; if (token.value && tripId.value && !historyMatch.value) void trip.load(token.value, tripId.value) }
addEventListener('popstate', onPop)
onUnmounted(() => removeEventListener('popstate', onPop))
function clearIdentity() { token.value = ''; credential.value = ''; trip.reset(); identityEpoch.value++; created.value = false }
function useCredential() {
  const next = credential.value.trim()
  clearIdentity()
  token.value = next
  if (token.value && tripId.value && !historyMatch.value) void trip.load(token.value, tripId.value)
}
async function save(input?: RequirementsInput) {
  const identity = identityEpoch.value
  await trip.save(token.value, input)
  if (identity === identityEpoch.value && trip.state.value.status === 'success') { created.value = true; navigate(`/trips/${trip.state.value.data.trip_id}`) }
}
function openPage(url: string) { trip.reset(); created.value = false; navigate(url); if (token.value && tripId.value && !historyMatch.value) void trip.load(token.value, tripId.value) }
function newTrip() { trip.reset(); created.value = false; identityEpoch.value++; navigate('/trips/new') }
</script>

<template>
  <a class="skip-link" href="#main">跳至主内容</a>
  <header class="app-header">
    <a class="brand" href="/" aria-label="EasyTravel 首页"><span class="brand-mark" aria-hidden="true">E<span>↗</span></span>EasyTravel</a>
    <nav aria-label="主导航"><a href="/trips/new" @click.prevent="newTrip">创建旅行</a><a href="#health-heading">服务状态</a></nav>
  </header>
  <main id="main" class="workspace">
    <div class="intro"><p class="eyebrow">EASYTRAVEL / 旅行需求</p><h1>{{ historyMatch ? '回看每次明确确认的旅程。' : '从你的旅行想法开始。' }}</h1><p class="intro-copy">{{ historyMatch ? '保存当时的行程、预算与披露，和当前工作稿清楚区分。' : '先保存需求，再一步步补充旅程的细节。' }}</p></div>
    <template v-if="localDevelopment">
      <section class="credential-panel" aria-labelledby="credential-heading">
        <h2 id="credential-heading">本地开发凭证</h2>
        <p class="scope-note">仅限本地开发，不能用于公开部署。Token 只保存在当前页面内存，刷新后需重新输入。</p>
        <form v-if="!token" @submit.prevent="useCredential"><label for="local-token">本地 Token</label><input id="local-token" v-model="credential" type="password" autocomplete="off" autocapitalize="off" spellcheck="false" /><button type="submit" :disabled="!credential.trim()">使用本地凭证</button></form>
        <template v-else><p>已提供本地凭证，身份有效性由后端验证。</p><button @click="clearIdentity">清除或更换凭证</button></template>
      </section>
      <template v-if="token">
        <TripHistory v-if="historyMatch && tripId" :key="identityEpoch" :token="token" :trip-id="tripId" :snapshot-id="historyMatch[2]" @navigate="openPage" />
        <RequirementsForm v-if="isNew" :key="identityEpoch" :busy="trip.state.value.status === 'loading'" @submit="save" />
        <p v-if="trip.state.value.status === 'loading'" role="status">{{ isNew ? '正在创建旅行，请勿重复提交。' : '正在从服务器读取旅行…' }}</p>
        <div v-if="trip.state.value.status === 'error'" class="notice form-error" role="alert">
          <p>{{ trip.state.value.message }}</p>
          <template v-if="isNew && trip.pending.value">
            <p>写请求取消或浏览器超时不代表服务器事务已撤销。原请求保留在本页内存；修改内容后提交会使用新 key，可能另建旅行。刷新后不会自动创建。</p>
            <button v-if="trip.retryAllowed.value" @click="save()">使用原 key 和原内容重试</button>
          </template>
          <button v-if="!isNew && tripId" @click="trip.load(token, tripId)">重新读取旅行</button>
        </div>
        <template v-if="trip.state.value.status === 'success'">
          <p v-if="created" role="status">旅行创建成功（HTTP 201）。</p>
          <TripWorkspace v-if="!historyMatch" @navigate="openPage" :key="`${identityEpoch}-${trip.state.value.data.trip_id}`" :initial="trip.state.value.data" :token="token" />
        </template>
      </template>
      <p v-else-if="!isNew">请重新输入本地凭证，从服务器读取此旅行。</p>
      <p v-if="!isNew && !tripId" role="alert">页面地址无效。请使用旅行页面 URL。</p>
    </template>
    <p v-else class="notice">本地开发旅行功能仅在本机开发服务器开放。此构建不提供本地 Token 登录。</p>
    <HealthStatus :state="state" @refresh="refresh" />
  </main>
  <footer class="app-footer"><span>EasyTravel</span><span>让每一步，都有据可循。</span></footer>
</template>
