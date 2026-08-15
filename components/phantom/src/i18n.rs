use std::sync::OnceLock;

pub fn lang() -> &'static str {
    static LANG: OnceLock<String> = OnceLock::new();
    LANG.get_or_init(|| {
        let raw = std::env::var("PHANTOM_LANG")
            .or_else(|_| std::env::var("PHANTOM_ROOM_LANG"))
            .unwrap_or_default()
            .to_lowercase();
        raw.chars().take(2).collect()
    })
}

pub fn l(de: &'static str, en: &'static str) -> &'static str {
    if lang() == "en" {
        en
    } else {
        de
    }
}
