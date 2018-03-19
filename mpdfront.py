#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Music Player Daemon Frontend. Adds a head to headless MPD.
"""

import sys, re, time, os, cgi
import argparse
import logging
import threading
import musicpd
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GdkPixbuf, GObject



log = logging.getLogger(__name__)
log.setLevel(logging.DEBUG)
formatter = logging.Formatter("%(asctime)s %(levelname)s %(threadName)s::%(funcName)s(%(lineno)d): %(message)s")
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(formatter)
log.addHandler(handler)


symbol_previous = chr(9612)+chr(9664)
symbol_rewind = chr(9664)+chr(9664)
symbol_stop = chr(9608)
symbol_play= chr(9613)+chr(9654)
symbol_pause = chr(9613)+chr(9613)
symbol_cue = chr(9654)+chr(9654)
symbol_next = chr(9654)+chr(9612)
default_window_width = 1280
default_window_height = 720
default_mpd_host = "localhost"
default_mpd_port = 6600

css_style = b"""
* {
    /*
    background-color: #a0a0a0;
    color: black;
    */
    border-style: solid;
    border-top-color: black;
    border-bottom-color: black;
    border-left-color: black;
    border-right-color: black;
}

window {
    background-color: #555555;
    color: white;
    font: 14pt arial;
}

grid#playback-pane label {
    padding: 0px 5px 10px 10px;
}

grid#playback-pane image{
    background-color: #404040;
}

scrolledwindow {
    padding: 5px 5px 5px 5px;
}

label#current-artist {
    font: bold 32pt arial;
}

label#current-title {
    font: 24pt arial;
}

label#current-album {
    font: 20pt arial;
}

label#current-time, label#end-time {
    font: bold 16pt arial;
}

button, button * {
    background: black;
    color: white;
}
button:active, button:active * {
    color: black;
    background: white;
}

button:focus, button:focus * {
    color: black;
    background: lightgray;
}

dialog, messagedialog {
    background-color: #b0b0b0;
    color: #000000;
    font: 16pt arial;
}

"""



def pp_time(secs):
    """
    Pretty-print time convenience function. Takes a count of seconds and formats to MM:SS.

    Args:
        secs: int of number of seconds

    Returns:
        string with the time in the format of MM:SS
    """
    return "%d:%02d" % (int(int(secs)/60), int(secs)%60)



class MetadataLabel(Gtk.Label):
    """
    Gtk.Label with 2 accessible variables: data and type.
    """
    def set_metadata(self, data):
        """
        Set the metadata for the label

        Args:
            data: can be anything
        """
        self.data = data

    def set_metatype(self, t):
        """
        Set the metatype for the label

        Args:
            t: can be anything
        """
        self.type = t



class IndexedListBox(Gtk.ListBox):
    """
    Gtk.ListBox with an index variable. This allows ListBoxes to track their position in list of ListBoxes.
    """
    def set_index(self, index):
        """
        Sets the index of the ListBox

        Args:
            index:  int, the ListBox's position in the parent's list
        """
        self.index = index



class ColumnBrowser(Gtk.Box):
    """
    Column browser for a tree data structure. Inherits from GtkBox.
    Creates columns with a list of GtkScrolledWindows containing a GtkListBox.
    """
    def __init__(self, selected_callback, keypress_callback, cols=2, spacing=5, hexpand=True, vexpand=True):
        """
        Constructor for the column browser.

        Args:
            selected_callback: callback function for handling row-selected events
            keypress_callback: callback function for handling key-press-event events
            cols: int for number of colums
            hexpand: boolean for whether to set horizontal expansion
            vexpand: boolean for whether to set vertical expansion
        """
        Gtk.Box.__init__(self)
        self.set_spacing(spacing)
        if cols < 1:
            raise Exception("Number of columns must be greater than 1")
        self.columns = []
        for i in range(0, cols):
            scroll = Gtk.ScrolledWindow()
            listbox = IndexedListBox()
            listbox.set_hexpand(hexpand)
            listbox.set_vexpand(vexpand)
            listbox.set_index(i)
            listbox.connect("row-selected", selected_callback)
            scroll.add(listbox)
            self.add(scroll)
            self.columns.append(listbox)

        self.connect("key-press-event", keypress_callback)


    def get_selected_rows(self):
        """
        Gets the child objects of all selected rows. 
        Inserting them into a list in order from least to highest column index.

        Returns:
            list of selected rows' child objects.
        """
        ret = []
        for c in self.columns:
            row = c.get_selected_row()
            if row:
                #metatype = row.type
                #value = row.get_text()
                child = row.get_child()
                ret.append({ 'type': child.type, 'value': child.get_text(), 'data': child.data  })
        return ret



    def set_column_data(self, col_index, data, clear_rest=True):
        """
        Populates a column at col_index.
        Args:
            col_index:  int, column index
            data:  dictionary
            clear_rest: boolean, True: clear all columns to the left as well. default: True
        """
        if clear_rest:
            for i in range(col_index, len(self.columns)):
                children = self.columns[i].get_children()
                if children:
                    for c in children:
                        self.columns[i].remove(c)
                        c.destroy()

        for i in data:
            #log.debug("data: %s" % i)
            label = MetadataLabel(i['value'])
            label.set_metatype(i['type'])
            label.set_metadata(i['data'])
            label.set_halign(Gtk.Align.START)
            self.columns[col_index].add(label)
        self.columns[col_index].show_all()



class MPCFront(Gtk.Window):
    """
    MPCFront(end). Adds a head to headless MPD. Meant to run locally with 
    full keyboard control that will translate remote controls.

    Caches artist/album/track information in dictionary db_cache.
    db_cache structure
        db_cache['Artist'][artist][album]        = list of dicts of song metadata
        db_cache['Album Artists'][artist][album] = ditto
        db_cache['Albums'][album]                = ditto
    """

    music_root_dir = "/Users/suresh/Music/nyquist"
    run_idle = True            ## Allows the ilde thread to run
    update_song_time = False   ## Allows song time to be updated

    ## Dialogs
    playlist_confirm_dialog = None
    edit_playlist_dialog = None
    song_info_dialog = None


    def __init__(self, host, port, css_style=None):
        """
        MPCFront constructor. Connects to MPD. Create main window and contained components.

        Args:
            host: string, hostname/IP of the MPD server
            port: int, TCP port of the MPD server
        """
        Gtk.Window.__init__(self, title="MPD - %s:%d" % (host, port))

        self.mpd_host = host
        self.mpd_port = port

        self.screen = Gdk.Screen.get_default()
        self.display = self.get_display()
        #self.monitor = self.display.get_primary_monitor()
        self.monitor = self.display.get_monitor(1)
        #self.monitor = self.display.get_monitor_at_window(self.get_window())
        log.debug("screen width: %s" % self.monitor.get_geometry().width)
        log.debug("screen height: %s" % self.monitor.get_geometry().height)

        if not self.mpd_connect():
            Gtk.main_quit()

        self.mpd_stats = self.mpd.stats()
        log.debug("mpd stats: %s" % self.mpd_stats)

        self.init_db_cache()

        ## topgrid is the toplevel layout container
        self.topgrid = Gtk.Grid()
        self.topgrid.set_hexpand(True)
        self.topgrid.set_vexpand(True)
        #self.topgrid.set_row_spacing(10)
        #self.topgrid.set_column_spacing(10)
        self.add(self.topgrid)

        ## Setup browser columns
        self.browser_box = ColumnBrowser(self.broswer_row_selected, self.browser_key_pressed, 4, 0)
        self.browser_box.set_name("browser")
        self.topgrid.attach(self.browser_box, 0, 0, 2, 1)
        rows = []
        for i in self.db_cache.keys():
            rows.append({ 'type': 'category', 'value': i, 'data': None})
        self.browser_box.set_column_data(0, rows)

        ## Setup playback grid
        self.playback_grid = Gtk.Grid()
        self.playback_grid.set_name("playback-pane")
        #self.playback_grid.set_row_spacing(10)
        #self.playback_grid.set_column_spacing(10)
        self.topgrid.attach(self.playback_grid, 0, 1, 1, 1)
        self.current_artist_label = Gtk.Label("Artist")
        self.current_artist_label.set_name("current-artist")
        self.current_artist_label.set_halign(Gtk.Align.START)
        self.current_artist_label.set_line_wrap(True)
        self.current_artist_label.set_hexpand(True)
        self.current_title_label = Gtk.Label("Title")
        self.current_title_label.set_name("current-title")
        self.current_title_label.set_halign(Gtk.Align.START)
        self.current_title_label.set_line_wrap(True)
        self.current_album_label = Gtk.Label("Album")
        self.current_album_label.set_name("current-album")
        self.current_album_label.set_halign(Gtk.Align.START)
        self.current_album_label.set_line_wrap(True)
        self.stats1_label = Gtk.Label("stats1")
        self.stats1_label.set_name("stats1")
        self.stats1_label.set_halign(Gtk.Align.START)
        self.stats2_label = Gtk.Label("stats2")
        self.stats2_label.set_name("stats2")
        self.stats2_label.set_halign(Gtk.Align.START)
        self.current_time_label = Gtk.Label("00:00")
        self.current_time_label.set_name("current-time")
        self.current_time_label.set_halign(Gtk.Align.START)
        self.end_time_label = Gtk.Label("00:00")
        self.end_time_label.set_name("end-time")
        self.end_time_label.set_halign(Gtk.Align.END)

        self.playback_grid.attach(self.current_artist_label, 0, 0, 1, 1)
        self.playback_grid.attach(self.current_title_label,  0, 1, 1, 1)
        self.playback_grid.attach(self.current_album_label,  0, 2, 1, 1)
        self.playback_grid.attach(self.stats1_label,         0, 3, 1, 1)
        self.playback_grid.attach(self.stats2_label,         0, 4, 1, 1)
        self.playback_grid.attach(self.current_time_label,   0, 5, 1, 1)
        self.playback_grid.attach(self.end_time_label,       1, 5, 1, 1)

        ## Setup playback button box
        self.playback_button_box = Gtk.Box()
        self.playback_grid.attach(self.playback_button_box, 0, 7, 2, 1)

        self.previous_button = Gtk.Button(symbol_previous)
        self.rewind_button = Gtk.Button(symbol_rewind)
        self.stop_button = Gtk.Button(symbol_stop)
        self.play_button = Gtk.Button(symbol_play)
        self.cue_button = Gtk.Button(symbol_cue)
        self.next_button = Gtk.Button(symbol_next)
        self.playback_button_box.pack_start(self.previous_button, True, True, 5)
        self.playback_button_box.pack_start(self.rewind_button, True, True, 5)
        self.playback_button_box.pack_start(self.stop_button, True, True, 5)
        self.playback_button_box.pack_start(self.play_button, True, True, 5)
        self.playback_button_box.pack_start(self.cue_button, True, True, 5)
        self.playback_button_box.pack_start(self.next_button, True, True, 5)

        self.song_progress = Gtk.LevelBar()
        self.playback_grid.attach(self.song_progress, 0, 6, 2, 1)

        self.current_albumart = Gtk.Image()
        self.current_albumart.set_vexpand(True)
        self.current_albumart.set_hexpand(True)
        self.playback_grid.attach(self.current_albumart, 1, 0, 1, 5)

        ## Setup playlist
        self.playlist_list = Gtk.ListBox()
        self.playlist_list.set_hexpand(True)
        self.playlist_list.set_vexpand(True)
        self.playlist_scroll = Gtk.ScrolledWindow()
        self.playlist_scroll.add(self.playlist_list)
        self.topgrid.attach(self.playlist_scroll, 1, 1, 1, 1)

        ## Set event handlers
        self.connect("delete-event", Gtk.main_quit)
        self.connect("destroy", Gtk.main_quit)
        self.connect("destroy-event", Gtk.main_quit)
        self.connect('key-press-event', self.key_pressed)
        self.previous_button.connect("clicked", self.previous_clicked)
        self.rewind_button.connect("clicked", self.rewind_clicked)
        self.stop_button.connect("clicked", self.stop_clicked)
        self.play_button.connect("clicked", self.play_clicked)
        self.cue_button.connect("clicked", self.cue_clicked)
        self.next_button.connect("clicked", self.next_clicked)
        self.playlist_list.connect("key-press-event", self.playlist_key_pressed)

        self.update_playback()
        self.update_playlist()
        self.set_resizable(True)
        self.present()
        self.get_focus()
        #self.set_size_request(self.monitor.get_geometry().width, self.monitor.get_geometry().height)

        self.spawn_idle_thread()
        self.playback_timeout_id = GObject.timeout_add(1000, self.playback_timeout, None)
        self.timeout_counter = 0

        """
        self.current_artist_label.modify_bg(Gtk.StateFlags.NORMAL, Gdk.Color(red=65535, blue=65535, green=65535))
        self.current_title_label.modify_bg(Gtk.StateFlags.NORMAL, Gdk.Color(red=65535, blue=65535, green=65535))
        self.current_album_label.modify_bg(Gtk.StateFlags.NORMAL, Gdk.Color(red=65535, blue=65535, green=65535))
        self.stats1_label.modify_bg(Gtk.StateFlags.NORMAL, Gdk.Color(red=65535, blue=65535, green=65535))
        self.stats2_label.modify_bg(Gtk.StateFlags.NORMAL, Gdk.Color(red=65535, blue=65535, green=65535))
        self.current_time_label.modify_bg(Gtk.StateFlags.NORMAL, Gdk.Color(red=65535, blue=65535, green=65535))
        self.end_time_label.modify_bg(Gtk.StateFlags.NORMAL, Gdk.Color(red=65535, blue=65535, green=65535))
        self.playback_button_box.modify_bg(Gtk.StateFlags.NORMAL, Gdk.Color(red=65535, blue=65535, green=65535))
        self.current_albumart.modify_bg(Gtk.StateFlags.NORMAL, Gdk.Color(red=65535, blue=65535, green=65535))
        """
        self.css = Gtk.CssProvider()
        self.css.load_from_data(css_style)
        self.style_context = self.get_style_context()
        self.style_context.add_provider_for_screen(Gdk.Screen.get_default(), self.css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        




##  BEGIN EVENT HANDLERS

##  Keyboard event handlers
    def key_pressed(self, widget, event):
        """
        Keypress handler for toplevel widget. Responds to global keys for playback control.
        """

        ctrl = (event.state & Gdk.ModifierType.CONTROL_MASK)
        mod1 = (event.state & Gdk.ModifierType.MOD1_MASK)
        mod2 = (event.state & Gdk.ModifierType.MOD2_MASK)

        try:
            if mod2 and event.keyval in (Gdk.KEY_q, Gdk.KEY_Q):
                Gtk.main_quit()
            elif event.keyval == ord(']'):
                log.debug("PLAY/PAUSE")
                self.play_or_pause()
            elif event.keyval == ord('['):
                log.debug("STOP")
                self.mpd.stop()
            elif event.keyval == ord('.'):
                log.debug("PREVIOUS")
                self.mpd.previous()
            elif event.keyval == ord('/'):
                log.debug("NEXT")
                self.mpd.next()
            elif event.keyval == ord(';'):
                log.debug("REWIND")
                self.mpd.seekcur("-5")
            elif event.keyval == ord("'"):
                log.debug("CUE")
                self.mpd.seekcur("+5")

            elif event.keyval == ord('1'):
                ## Focus on the last selected row in the browser
                selected_items = self.browser_box.get_selected_rows()
                if not len(selected_items):
                    self.browser_box.columns[0].select_row(self.browser_box.columns[0].get_children()[0])
                    selected_items = self.browser_box.get_selected_rows()
                focus_col = self.browser_box.columns[len(selected_items)-1]
                focus_row = focus_col.get_selected_row()
                focus_row.grab_focus()

            elif event.keyval == ord('2'):
                ## Focus on the selected row in the playlist
                selected_row = self.playlist_list.get_selected_row()
                if not selected_row:
                    selected_row = self.playlist_list.get_children()[0]
                    self.playlist_list.select_row(self.playlist_list.get_children()[0])
                selected_row.grab_focus()

            #elif event.keyval == Gdk.KEY_Right:
            #    log.debug("RIGHT")
            #elif event.keyval == Gdk.KEY_Left:
            ##    log.debug("LEFT")
            #elif event.keyval == Gdk.KEY_Up:
            #    log.debug("UP")
            #elif event.keyval == Gdk.KEY_Down:
            #    log.debug("DOWN")
            #else:
            #    log.debug("key press: %s" % event.keyval)

        except (musicpd.ConnectionError, BrokenPipeError) as e:
            log.info("previous mpd command failed: %s" % e)
            self.mpd_connect()

        except Exception as e:
            log.error("Unknown exception: %s" % e)

    def browser_key_pressed(self, widget, event):
        if event.keyval == Gdk.KEY_Return:
            log.debug("browser key: ENTER")
            self.add_to_playlist()

        elif event.keyval == ord('\\'):
            self.browser_info_popup()


    def playlist_key_pressed(self, widget, event):
        if event.keyval == Gdk.KEY_Return:
            log.debug("playlist key: ENTER")
            self.edit_playlist()

        elif event.keyval == ord('\\'):
            self.playlist_info_popup()

##  Click handlers

    def previous_clicked(self, button):
        """
        Click handler for previous button
        """
        log.debug("PREVIOUS")
        try:
            self.mpd.previous()
        except (musicpd.ConnectionError, BrokenPipeError) as e:
            log.info("previous failed: %s" % e)
            self.mpd_connect()
            self.mpd.previous()

    def rewind_clicked(self, button):
        """
        Click handler for rewind button
        """
        log.debug("REWIND")
        try:
            self.mpd.seekcur("-5")
        except (musicpd.ConnectionError, BrokenPipeError) as e:
            log.info("rewind failed: %s" % e)
            self.mpd_connect()
            self.mpd.seekcur("-5")

    def stop_clicked(self, button):
        """
        Click handler for stop button
        """
        log.debug("STOP")
        try:
            self.mpd.stop()
        except (musicpd.ConnectionError, BrokenPipeError) as e:
            log.info("stop failed: %s" % e)
            self.mpd_connect()
            self.mpd.stop()

    def play_clicked(self, button):
        """
        Click handler for play/pause button
        """
        self.play_or_pause()

    def cue_clicked(self, button):
        """
        Click handler for cue button
        """
        log.debug("CUE")
        try:
            self.mpd.seekcur("+5")
        except (musicpd.ConnectionError, BrokenPipeError) as e:
            log.info("cue failed: %s" % e)
            self.mpd_connect()
            self.mpd.seekcur("+5")

    def next_clicked(self, button):
        """
        Click handler for next button
        """
        log.debug("NEXT")
        try:
            self.mpd.next()
        except (musicpd.ConnectionError, BrokenPipeError) as e:
            log.info("next failed: %s" % e)
            self.mpd_connect()
            self.mpd.next()

##  Selected handlers

    def broswer_row_selected(self, listbox, row):
        """
        Handler for selection event in browser_box
        """
        if not row:
            return

        child = row.get_child()
        if child:
            metatype = child.type
            value = child.get_text()
            log.debug("col %d, %s: %s" % (listbox.index, metatype, value))
            if metatype == "category":
                if value == "Album Artists":
                    artists = self.get_albumartists()
                    log.debug("albumartists: %s" % artists)
                    rows = []
                    for a in artists:
                        rows.append({ 'type': 'albumartist', 'value': a, 'data': None })
                    self.browser_box.set_column_data(listbox.index+1, rows)

                elif value == "Artists":
                    artists = self.get_artists()
                    log.debug("artists: %s" % artists)
                    rows = []
                    for a in artists:
                        rows.append({ 'type': 'artist', 'value': a, 'data': None })
                    self.browser_box.set_column_data(listbox.index+1, rows)

                elif value == "Albums":
                    albums = self.get_albums()
                    rows = []
                    for a in albums:
                        rows.append({ 'type': 'album', 'value': a, 'data': None })
                    self.browser_box.set_column_data(listbox.index+1, rows)

                elif value == "Genres":
                    genres = self.get_genres()
                    rows = []
                    for g in genres:
                        rows.append({'type': 'genre', 'value': g, 'data': None})
                    self.browser_box.set_column_data(listbox.index+1, rows)

                    """
                elif value == "Files":
                    files = self.mpd.listfiles()
                    rows = []
                    for f in files:
                        rows.append({'type': 'file', 'value': f, 'data': None})
                    self.browser_box.set_column_data(listbox.index+1, rows)
                    """

                else:
                    self.browser_box.set_column_data(listbox.index+1, [])

            elif metatype == "albumartist":
                albums = self.get_albums_by_albumartist(value)
                #log.debug("albums: %s" % albums)
                rows = []
                for a in albums:
                    rows.append({ 'type': 'album', 'value': a, 'data': None })
                self.browser_box.set_column_data(listbox.index+1, rows)
    
            elif metatype == "artist":
                albums = self.get_albums_by_artist(value)
                #log.debug("albums: %s" % albums)
                rows = []
                for a in albums:
                    rows.append({ 'type': 'album', 'value': a, 'data': None })
                self.browser_box.set_column_data(listbox.index+1, rows)

            elif metatype == "genre":
                albums = self.get_albums_by_genre(value)
                log.debug("albums: %s" % albums)
                rows = []
                for a in albums:
                    rows.append({ 'type': 'album', 'value': a, 'data': None })
                self.browser_box.set_column_data(listbox.index+1, rows)
    
            elif metatype == "album":
                selected_items = self.browser_box.get_selected_rows()
                log.debug("selected items: %s" % selected_items)
                last_type = selected_items[listbox.index-1]['type']
                last_value = selected_items[listbox.index-1]['value']
                log.debug("%s %s" % (value, last_value))
                songs = None
                if last_type == "albumartist":
                    songs = self.get_songs_by_album_by_albumartist(value, last_value)

                elif last_type == "artist":
                    songs = self.get_songs_by_album_by_artist(value, last_value)

                elif last_type == "category":
                    songs = self.get_songs_by_album(value)

                elif last_type == "genre":
                    songs = self.get_songs_by_album_by_genre(value, last_value)

                rows = []
                if songs:
                    for s in songs:
                        #log.debug(s)
                        track = re.sub(r'/.*', '',  s['track'])
                        rows.append({ 'type': 'song', 'value': track+" "+ s['title'], 'data': s })
                self.browser_box.set_column_data(listbox.index+1, rows)

##  END EVENT HANDLERS



    def mpd_connect(self):
        """
        Connect to MPD. Requires mpd_host and mpd_port.

        Returns:
            boolean, True if connected, False if not
        """
        try:
            self.mpd = musicpd.MPDClient()
            self.mpd.connect(self.mpd_host, self.mpd_port)
            log.debug("connected to %s:%d" % (self.mpd_host, self.mpd_port))
        except Exception as e:
            log.fatal("Could not connect to mpd %s:%d: %s" % (self.mpd_host, self.mpd_port, e))
            return False
        return True



    def init_db_cache(self):
        self.db_cache = {}
        self.db_cache['Album Artists'] = {}
        self.db_cache['Artists'] = {}
        self.db_cache['Albums'] = {}
        self.db_cache['Files'] = {}
        self.db_cache['Genres'] = {}
        #self.db_cache['Songs'] = {}



    def play_or_pause(self):
        """
        Check the player status, play if stopped, pause otherwise.
        """
        if self.mpd_status['state'] == "stop":
            log.debug("PLAY")
            try:
                self.mpd.play()
            except (musicpd.ConnectionError, BrokenPipeError) as e:
                log.info("play failed: %s" % e)
                self.mpd_connect()
                self.mpd.play()
        else:
            log.debug("PAUSE")
            try:
                self.mpd.pause()
            except (musicpd.ConnectionError, BrokenPipeError) as e:
                log.info("pause failed: %s" % e)
                self.mpd_connect()
                self.mpd.pause()



    def set_current_albumart(self):
        song_dir = os.path.dirname(self.mpd_currentsong['file'])
        cover_path = self.music_root_dir+"/"+song_dir+"/cover.jpg"
        log.debug("looking for cover file: %s" % cover_path)
        if not os.path.isfile(cover_path):
            cover_path = "cover.jpg"
        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(cover_path, 200, 200, True)
        self.current_albumart.set_from_pixbuf(pixbuf)



    def update_playback(self, mpd=None):
        """
        Updates playback display
        """
        if not mpd:
            mpd = self.mpd
        try:
            self.mpd_status = mpd.status()
            self.mpd_currentsong = mpd.currentsong()
            log.debug("status: %s" % self.mpd_status)
            log.debug("currentsong: %s" % self.mpd_currentsong)
        except (musicpd.ConnectionError, BrokenPipeError) as e:
            log.debug("Attempting reconnect: %s" % e)
            self.mpd_connect()
            self.mpd_status = mpd.status()
            self.mpd_currentsong = mpd.currentsong()


        if self.mpd_currentsong:
            self.current_artist_label.set_text(self.mpd_currentsong['artist'])
            self.current_title_label.set_text(self.mpd_currentsong['title'])
            self.current_album_label.set_text(self.mpd_currentsong['album'])

        if 'audio' in self.mpd_status.keys():
            self.stats1_label.set_text(self.mpd_status['audio'])

        if 'bitrate' in self.mpd_status.keys():
            self.stats2_label.set_text(self.mpd_status['bitrate'])

        if 'time' in self.mpd_status.keys():
            self.mpd_status['curr_t'], self.mpd_status['end_t'] = self.mpd_status['time'].split(r':', 1)
            self.song_progress.set_max_value(int(self.mpd_currentsong['time']))
            self.song_progress.set_value(int(self.mpd_status['curr_t']))
            self.current_time_label.set_text(pp_time(self.mpd_status['curr_t']))
            self.end_time_label.set_text(pp_time(self.mpd_status['end_t']))

        self.set_current_albumart()



    def get_playlist(self, mpd=None):
        """
        Query for playlist. Clean up data before returning.

        Returns:
            list of filenames
        """
        if not mpd:
            mpd = self.mpd
        try:
            plist = mpd.playlistinfo()
            log.debug("playlist: %s" % plist)
        except (musicpd.ConnectionError, BrokenPipeError) as e:
            log.error("could not fetch playlist: %s" % e)
            self.mpd_connect()
            return None
        return plist
        playlist = []
        for song in plist:
            #log.debug("file_info: %s" % song)
            playlist.append(song)
        return playlist



    def update_playlist(self, mpd=None):
        """
        Updates playlist display. Makes MPD call for the playlist. 
        Clears the current playlist. Adds song titles to the listbox.
        """
        if not mpd:
            mpd = self.mpd
        playlist = self.get_playlist(mpd)
        log.debug("playlist: %s" % playlist)
        if not playlist:
            return

        ## Empty list if aleady populated
        children = self.playlist_list.get_children()
        if children:
            for c in children:
                self.playlist_list.remove(c)
                c.destroy()

        ## Add songs to the list
        for song in playlist:
            label_text = re.sub(r'/.*', '', song['track'])+" ("+pp_time(song['time'])+") <b>"+song['title']+"</b>"
            label = MetadataLabel()
            label.set_markup(label_text)
            label.set_metatype('song')
            label.set_metadata(song)
            label.set_halign(Gtk.Align.START)
            self.playlist_list.add(label)

        self.playlist_list.show_all()


    def idle_thread(self):
        """
        Function that runs in the idle thread created by spawn_idle_thread()
        """
        mpd = musicpd.MPDClient()
        try:
            mpd.connect(self.mpd_host, self.mpd_port)
        except (musicpd.ConnectionError, BrokenPipeError) as e:
            log.critical("idle thread could not connect to MPD: %s" % e)
            return None

        while self.run_idle:
            try:
                mpd.send_idle()
                changes = mpd.fetch_idle()
            except (musicpd.ConnectionError, BrokenPipeError) as e:
                log.error("idle failed: %s" % e)
                self.mpd_connect()
                continue
            except Exception as e:
                log.error("idle failed: %s" % e)
            self.update_song_time = False
            log.debug("changes: %s" % changes)
            for c in changes:
                if c == "playlist":
                    self.update_playlist(mpd)
                elif c == "player":
                    self.update_playback(mpd)
                elif c == "database":
                    self.init_db_cache()
                else:
                    log.info("Unhandled change: %s" % c)
            self.update_song_time = True
            GObject.source_remove(self.playback_timeout_id)
            self.playback_timeout_id = GObject.timeout_add(1000, self.playback_timeout, None)
            


    def spawn_idle_thread(self):
        """
        Creates and starts the idle thread that listens for change events from MPD.
        """
        try:
            idle_thread = threading.Thread(target=self.idle_thread, args=(), name="idle_update")
            idle_thread.daemon = True
            idle_thread.start()
        except Exception as e:
            log.fatal("Could not spawn idle thread: %s" % e)
            return None
        return True



    def get_artists(self, mpd=None):
        """
        Gets the list of artists from mpd, enters artists into db_cache if needed

        Args:
            mpd: Optional musicpd.MPDClient object. If not supplied, the object's mpd object will be used.

        Returns:
            list of artist names
        """
        if not mpd:
            mpd = self.mpd

        if not len(self.db_cache['Artists']):
            try:
                recv = mpd.list("artist")
                log.debug("artists: %s" % recv)
            except (musicpd.ConnectionError, BrokenPipeError) as e:
                log.fatal("get artists failed: %s" % e)
                self.mpd_connect()
                return None

            for i in recv:
                if i == "":
                    continue
                log.debug("Adding to cache artist: %s" % i)
                self.db_cache['Artists'][i] = {}

        return self.db_cache['Artists'].keys()



    def get_albumartists(self, mpd=None):
        """
        Gets the list of albumartists from mpd, enters albumartists into db_cache if needed

        Args:
            mpd: Optional musicpd.MPDClient object. If not supplied, the object's mpd object will be used.

        Returns:
            list of artist names
        """
        if not mpd:
            mpd = self.mpd

        if not len(self.db_cache['Album Artists']):
            try:
                recv = mpd.list("albumartist")
                log.debug("albumartists: %s" % recv)
            except (musicpd.ConnectionError, BrokenPipeError) as e:
                log.fatal("get albumartists failed: %s" % e)
                self.mpd_connect()
                return None

            for i in recv:
                if i == "":
                    continue
                log.debug("Adding to cache albumartist: %s" % i)
                self.db_cache['Album Artists'][i] = {}

        return self.db_cache['Album Artists'].keys()



    def get_albums(self, mpd=None):
        """
        Gets the list of albums from mpd, enters albums into db_cache if needed

        Args:
            mpd: Optional musicpd.MPDClient object. If not supplied, the object's mpd object will be used.

        Returns:
            list of album names
        """
        if not mpd:
            mpd = self.mpd

        if not len(self.db_cache['Albums']):
            try:
                recv = mpd.list("album")
                log.debug("albums: %s" % recv)
            except (musicpd.ConnectionError, BrokenPipeError) as e:
                log.fatal("get albums failed: %s" % e)
                self.mpd_connect()
                return None

            for i in recv:
                if i == "":
                    continue
                log.debug("Adding to cache album: %s" % i)
                self.db_cache['Albums'][i] = {}

        return self.db_cache['Albums'].keys()



    def get_albums_by_artist(self, artist, mpd=None):
        """
        Gets the list of albums by an artist from mpd, enters albums into db_cache if needed

        Args:
            mpd: Optional musicpd.MPDClient object. If not supplied, the object's mpd object will be used.

        Returns:
            list of album names by an artist
        """
        if not mpd:
            mpd = self.mpd
        if not artist in self.db_cache['Artists']:
            self.db_cache['Artists'][artist] = {}

        if not len(self.db_cache['Artists'][artist]):
            try:
                recv = mpd.list("album", artist)
                log.debug("albums: %s" % recv)
            except (musicpd.ConnectionError, BrokenPipeError) as e:
                log.fatal("get albums by artist failed: %s" % e)
                self.mpd_connect()
                return None

            for i in recv:
                if i== "":
                    continue
                log.debug("Adding to cache artist / album: %s / %s" % (artist, i))
                self.db_cache['Artists'][artist][i] = []

        return self.db_cache['Artists'][artist]



    def get_albums_by_albumartist(self, artist, mpd=None):
        """
        Gets the list of albums by an albumartist from mpd, enters albums into db_cache if needed

        Args:
            mpd: Optional musicpd.MPDClient object. If not supplied, the object's mpd object will be used.

        Returns:
            list of album names by an albumartist
        """
        if not mpd:
            mpd = self.mpd
        if not artist in self.db_cache['Album Artists']:
            self.db_cache['Album Artists'][artist] = {}

        if not len(self.db_cache['Album Artists'][artist]):
            try:
                recv = mpd.list("album", "albumartist", artist)
                log.debug("albums: %s" % recv)
            except (musicpd.ConnectionError, BrokenPipeError) as e:
                log.fatal("get albums by albumartist failed: %s" % e)
                self.mpd_connect()
                return None

            for i in recv:
                if i== "":
                    continue
                log.debug("Adding to cache artist / album: %s / %s" % (artist, i))
                self.db_cache['Album Artists'][artist][i] = []

        return self.db_cache['Album Artists'][artist]



    def get_songs_by_album_by_artist(self, album, artist, mpd=None):
        """
        Finds songs by album and artist.

        Args:
            album: name of the album
            artist: name of the artist
            mpd: Optional musicpd.MPDClient object. If not supplied, the object's mpd object will be used.

        Returns:
            list of dictionaries containing song data
        """
        if not mpd:
            mpd = self.mpd

        if not len(self.db_cache['Artists'][artist][album]):
            try:
                recv = self.mpd.find("artist", artist, "album", album)
                log.debug("songs: %s" % recv)
            except (musicpd.ConnectionError, BrokenPipeError) as e:
                log.fatal("get songs by album by artist failed: %s" % e)
                self.mpd_connect()
                return None

            for i in recv:
                if i== "":
                    continue
                log.debug("Adding to cache artist / album / song: %s / %s / %s" % (artist, album, i))
                self.db_cache['Artists'][artist][album].append(i)

        return self.db_cache['Artists'][artist][album]



    def get_songs_by_album_by_genre(self, album, genre, mpd=None):
        """
        Finds songs by album and artist.

        Args:
            album: name of the album
            genre: name of the genre
            mpd: Optional musicpd.MPDClient object. If not supplied, the object's mpd object will be used.

        Returns:
            list of dictionaries containing song data
        """
        if not mpd:
            mpd = self.mpd

        if not len(self.db_cache['Genres'][genre][album]):
            try:
                recv = self.mpd.find("genre", genre, "album", album)
                log.debug("songs: %s" % recv)
            except (musicpd.ConnectionError, BrokenPipeError) as e:
                log.fatal("get songs by album by genre failed: %s" % e)
                self.mpd_connect()
                return None

            for i in recv:
                if i== "":
                    continue
                log.debug("Adding to cache genre / album / song: %s / %s / %s" % (genre, album, i))
                self.db_cache['Genres'][genre][album].append(i)

        return self.db_cache['Genres'][genre][album]



    def get_songs_by_album_by_albumartist(self, album, artist, mpd=None):
        """
        Finds songs by album and albumartist.

        Args:
            album: name of the album
            artist: name of the albumartist
            mpd: Optional musicpd.MPDClient object. If not supplied, the object's mpd object will be used.

        Returns:
            list of dictionaries containing song data
        """
        if not mpd:
            mpd = self.mpd

        if not len(self.db_cache['Album Artists'][artist][album]):
            try:
                recv = self.mpd.find("albumartist", artist, "album", album)
                log.debug("songs: %s" % recv)
            except (musicpd.ConnectionError, BrokenPipeError) as e:
                log.fatal("get songs by album by albumartist failed: %s" % e)
                self.mpd_connect()
                return None

            for i in recv:
                if i== "":
                    continue
                log.debug("Adding to cache albumartist / album / song: %s / %s / %s" % (artist, album, i))
                self.db_cache['Album Artists'][artist][album].append(i)

        return self.db_cache['Album Artists'][artist][album]



    def get_songs_by_album(self, album, mpd=None):
        """
        Finds songs by album.

        Args:
            album: name of the album
            mpd: Optional musicpd.MPDClient object. If not supplied, the object's mpd object will be used.

        Returns:
            list of dictionaries containing song data
        """
        if not mpd:
            mpd = self.mpd

        if not len(self.db_cache['Albums'][album]):
            self.db_cache['Albums'][album] = []
            try:
                recv = self.mpd.find("album", album)
                log.debug("songs: %s" % recv)
            except (musicpd.ConnectionError, BrokenPipeError) as e:
                log.fatal("get songs by album failed: %s" % e)
                self.mpd_connect()
                return None

            for i in recv:
                if i== "":
                    continue
                log.debug("Adding to cache album / song: %s / %s" % (album, i))
                self.db_cache['Albums'][album].append(i)

        return self.db_cache['Albums'][album]



    def get_genres(self, mpd=None):
        """
        Gets the list of genres from mpd, enters artists into db_cache if needed

        Args:
            mpd: Optional musicpd.MPDClient object. If not supplied, the object's mpd object will be used.

        Returns:
            list of genres
        """
        if not mpd:
            mpd = self.mpd

        if not len(self.db_cache['Genres']):
            try:
                recv = mpd.list("genre")
                log.debug("genres: %s" % recv)
            except (musicpd.ConnectionError, BrokenPipeError) as e:
                log.fatal("get genres failed: %s" % e)
                self.mpd_connect()
                return None

            for i in recv:
                if i == "":
                    continue
                log.debug("Adding to cache genres: %s" % i)
                self.db_cache['Genres'][i] = {}

        return self.db_cache['Genres'].keys()



    def get_albums_by_genre(self, genre, mpd=None):
        """
        """
        if not mpd:
            mpd = self.mpd
        if not len(self.db_cache['Genres'][genre]):
            try:
                recv = self.mpd.list("album", "genre", genre)
                log.debug("albums: %s" % recv)
            except (musicpd.ConnectionError, BrokenPipeError) as e:
                log.fatal("get album by genre: %s" % e)
                self.mpd_connect()
            for i in recv:
                if i == "":
                    continue
                log.debug("Adding to cache genres / album: %s / %s" % (genre, i))
                self.db_cache['Genres'][genre][i] = []

        return self.db_cache['Genres'][genre]



    def add_to_playlist(self):
        """
        Displays confirmation dialog, presenting options to add, replace or cancel.
        """
        selected_items = self.browser_box.get_selected_rows()
        #log.debug("selected items: %s" % selected_items)
        if not selected_items[-1]['type'] in ("album", "song"):
            return
        add_item_name = ""
        for i in range(1, len(selected_items)):
            if selected_items[i]['type'] == "song":
                add_item_name += selected_items[i]['data']['title']+" "
            else:
                add_item_name += selected_items[i]['value']+" "

        self.playlist_confirm_dialog = Gtk.Dialog("Update playlist?", self, Gtk.DialogFlags.MODAL, ("Add", 1, "Replace", 2, "Cancel", -4))
        self.playlist_confirm_dialog.get_content_area().add(Gtk.Label("Selected: "+add_item_name))
        self.playlist_confirm_dialog.get_content_area().set_size_request(300, 100)
        style_context = self.playlist_confirm_dialog.get_style_context()
        style_context.add_provider(self.css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.playlist_confirm_dialog.show_all()
        response = self.playlist_confirm_dialog.run()
        self.playlist_confirm_dialog.destroy()
        log.debug("dialog response: %s" % response)

        try:
            if response == 2:
                ## Clear list before adding for "replace"
                #self.mpd.clear()
                None

            if response in (1, 2):
                if selected_items[-1]['type'] == "song":
                    #log.debug("adding song: %s" % selected_items[-1]['data']['title'])
                    self.mpd.add(selected_items[-1]['data']['file'])
                elif selected_items[-1]['type'] == "album":
                    log.debug("adding album: %s" % selected_items[-1]['value'])
                    if selected_items[-2]['type'] == "artist":
                        self.mpd.findadd("artist", selected_items[-2]['value'], "album", selected_items[-1]['value'])
                    elif selected_items[-2]['type'] == "albumartist":
                        self.mpd.findadd("albumartist", selected_items[-2]['value'], "album", selected_items[-1]['value'])
                    elif selected_items[-2]['type'] == "genre":
                        self.mpd.findadd("genre", selected_items[-2]['value'], "album", selected_items[-1]['value'])


        except (musicpd.ConnectionError, BrokenPipeError) as e:
            log.error("adding to playlist: %s" % e)
            self.mpd_connect()



    def edit_playlist(self):
        """
        Displays dialog with playlist edit options. Performs task based on user input.
        """
        index = self.playlist_list.get_selected_row().get_index()
        song = self.playlist_list.get_selected_row().get_child().data
        log.debug("selected song: %s" % song)

        self.edit_playlist_dialog = Gtk.Dialog("Edit playlist", self, Gtk.DialogFlags.MODAL, ("Up", 1, "Down", 2, "Delete", 3, "Cancel", -4))
        self.edit_playlist_dialog.get_content_area().add(Gtk.Label("Edit: "+song['title']))
        self.edit_playlist_dialog.get_content_area().set_size_request(300, 100)
        style_context = self.edit_playlist_dialog.get_style_context()
        style_context.add_provider(self.css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.edit_playlist_dialog.show_all();
        response = self.edit_playlist_dialog.run()
        self.edit_playlist_dialog.destroy()
        log.debug("dialog response: %s" % response)

        try:
            if response == 1:
                log.debug("Moving song up 1 place from %d" % index)
                if index > 0:
                    self.mpd.moveid(song['id'], index-1)
            elif response == 2:
                log.debug("Moving song down 1 place from %d" % index)
                self.mpd.moveid(song['id'], index+1)
            elif response == 3:
                log.debug("Deleting song at %d" % index)
                self.mpd.deleteid(song['id'])

        except (musicpd.ConnectionError, BrokenPipeError) as e:
            log.error("editing playlist: %s" % e)
            self.mpd_connect()
        except Exception as e:
            log.error("editing playlist: %s" % e)



    def playback_timeout(self, data):
        """
        Calls to update playback info, resets timeout.

        """
        timeout = 0
        if self.mpd_status['state'] in ("stop", "pause"):
            log.debug("not playing, sleeping")
            timeout = 10000
        elif self.mpd_status['state'] == "play":
            self.timeout_counter += 1
            if self.timeout_counter >= 5:
                self.update_playback()
                self.timeout_counter = 0
            else:
                self.mpd_status['curr_t'] = str(int(self.mpd_status['curr_t'])+1)
                self.song_progress.set_value(int(self.mpd_status['curr_t']))
                self.current_time_label.set_text(pp_time(self.mpd_status['curr_t']))
            timeout = 1000
        else:
            log.info("unknown state: %s" % self.mpd_status['state'])
        self.playback_timeout_id = GObject.timeout_add(timeout, self.playback_timeout, None)



    def song_info_popup(self, song):
        """
        """
        song_text = """<big><b>%s</b></big>
Artist: <b>%s</b>
Album: %s
Time: %s
Track: %s
Date: %s
        """ % (cgi.escape(song['title']), cgi.escape(song['artist']), cgi.escape(song['album']), pp_time(song['time']), song['track'], song['date'], )
        self.song_info_dialog = Gtk.MessageDialog(self, Gtk.DialogFlags.MODAL, Gtk.MessageType.INFO, Gtk.ButtonsType.OK)
        self.song_info_dialog.set_name("song-info")
        self.song_info_dialog.set_markup(song_text)
        self.song_info_dialog.get_content_area().set_size_request(300, 300)
        style_context = self.song_info_dialog.get_style_context()
        style_context.add_provider(self.css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.song_info_dialog.show_all()
        self.song_info_dialog.run()
        self.song_info_dialog.destroy()



    def playlist_info_popup(self):
        """
        """
        song = self.playlist_list.get_selected_row().get_child().data
        self.song_info_popup(song)



    def browser_info_popup(self):
        """
        """
        song = self.browser_box.get_selected_rows()[-1]['data']
        self.song_info_popup(song)






if __name__ == "__main__":
    arg_parser = argparse.ArgumentParser(description="MPD Frontend", formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    #arg_parser.add_argument("-v", "--verbose", action='store_true', help="Turn on verbose output.")
    arg_parser.add_argument("-H", "--host", default=default_mpd_host, action='store', help="Remote host name or IP address.")
    arg_parser.add_argument("-p", "--port", default=default_mpd_port, type=int, action='store', help="Remote TCP port number.")
    args = arg_parser.parse_args()

    window = MPCFront(args.host, args.port, css_style)
    window.set_size_request(default_window_width, default_window_height)
    window.present()
    window.show()
    window.show_all()
    Gtk.main()
    sys.exit(0)

