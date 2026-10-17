package com.example.agentdemo.domain.profile

/**
 * Порт хранилища профилей пользователя (слой персонализации).
 *
 * Хранит набор профилей и указатель на активный. Отдельное хранилище от памяти
 * (диалога, рабочей и долговременной) — персонализация физически независима от
 * того, что ассистент знает. Реализацию (JSON-файл) поставляет слой data.
 */
interface UserProfileRepository {

    /** Все сохранённые профили. Пустой список — профилей ещё нет. */
    suspend fun loadAll(): List<UserProfile>

    /** Id активного профиля (`null`, если профиля нет или он не выбран). */
    suspend fun activeId(): String?

    /** Создать или перезаписать профиль (по [UserProfile.id]). */
    suspend fun save(profile: UserProfile)

    /** Удалить профиль по id. Если он был активным — активный сбрасывается. */
    suspend fun delete(id: String)

    /** Сделать профиль активным — именно он уходит в запросы. */
    suspend fun setActive(id: String)
}
