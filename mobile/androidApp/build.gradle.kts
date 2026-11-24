plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
}

fun prop(name: String): String = providers.gradleProperty(name).get()

android {
    namespace = "com.tennisvision.app"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.tennisvision.app"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "0.1.0"
        buildConfigField("String", "API_BASE_URL", "\"${prop("tennis.apiBaseUrl")}\"")
        buildConfigField("String", "OIDC_ISSUER", "\"${prop("tennis.oidcIssuer")}\"")
        buildConfigField("String", "OIDC_CLIENT_ID", "\"${prop("tennis.oidcClientId")}\"")
        // Debe coincidir con la redirect URI del cliente tennis-mobile en Keycloak.
        manifestPlaceholders["appAuthRedirectScheme"] = "com.tennisvision.app"
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
}

dependencies {
    implementation(project(":shared"))
    implementation(platform(libs.compose.bom))
    implementation(libs.compose.material3)
    implementation(libs.compose.ui)
    implementation(libs.compose.ui.tooling.preview)
    implementation(libs.activity.compose)
    implementation(libs.lifecycle.viewmodel.compose)
    implementation(libs.lifecycle.runtime.compose)
    implementation(libs.appauth)
    implementation(libs.media3.exoplayer)
    implementation(libs.media3.ui)
    implementation(libs.kotlinx.coroutines.core)
}
