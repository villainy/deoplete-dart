if exists('g:loaded_deoplete_dart')
  finish
endif
let g:loaded_deoplete_dart = 1

let g:deoplete#sources#dart#dart_sdk_path =
      \ get(g:, 'deoplete#sources#dart#dart_sdk_path', '')

let g:deoplete#sources#dart#on_event = 
      \ get(g:, 'deoplete#sources#dart#on_event', 0)
