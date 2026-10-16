import java.io.FileInputStream
import java.util.Properties

plugins {
    alias(libs.plugins.android.library)
    alias(libs.plugins.kotlin.serialization)
}

android {
    namespace = "com.example.agentdemo.data"
    compileSdk = 36

    defaultConfig {
        minSdk = 24
        // Ключ Z.ai прокидывается из корневого .env в BuildConfig data-модуля —
        // так секрет остаётся в слое данных и не протекает в UI/домен.
        buildConfigField("String", "ZAI_API_KEY", "\"${readEnv()["API_KEY"] ?: ""}\"")
    }

    buildFeatures {
        buildConfig = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

kotlin {
    jvmToolchain(17)
}

dependencies {
    implementation(project(":domain"))

    implementation(libs.kotlinx.coroutines.core)
    implementation(libs.kotlinx.serialization.json)
    implementation(libs.okhttp)
    implementation(platform(libs.koin.bom))
    implementation(libs.koin.core)
    implementation(libs.koin.android) // androidContext() для доступа к filesDir в хранилище истории

    testImplementation(libs.junit)
    testImplementation(libs.kotlinx.coroutines.test)
}

fun readEnv(): Map<String, String> {
    val envFile = rootProject.file(".env")
    if (!envFile.exists()) return emptyMap()
    val props = Properties()
    FileInputStream(envFile).use { props.load(it) }
    return props.entries.associate { (k, v) -> k.toString() to v.toString() }
}
