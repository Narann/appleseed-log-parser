import datetime
import csv
import re
import os.path
import sys
from PyQt4 import uic, QtGui, QtCore
import functools

################################################################################
# Prepare regex
################################################################################
re_main = re.compile( '(?P<timestamp>[0-9TZ:.-]+)\s+'
                      '\<(?P<thread_id>\d{3})\>\s+'
                      '(?P<vm>\d+)\s+MB\s+'
                      '(?P<msg_cat>\w+)\s+'
                      '\|(?P<msg_rest>.*)\n?' )

re_opening_texture_file = re.compile( '\s*opening texture file (?P<texture_path>[\w./\\\\-]+) for reading\.\.\.' )
re_rendering_progress   = re.compile( '\s*rendering\, (?P<percentage>[\d.]+)\% done' )
re_wrote_image_file     = re.compile( '\s*wrote image file (?P<image_path>[\w./\\\\-]+) in (?P<milliseconds>[\d,]+) ms\.' )

re_project_file_path    = re.compile( '\s*loading project file (?P<project_file_path>[\w./\\\\-]+)\.\.\.' )
re_loaded_mesh_file     = re.compile( '\s*loaded mesh file (?P<mesh_path>[\w./\\\\-]+) '
                                      '\((?P<objects>[\d,]+) object, '
                                      '(?P<vertices>[\d,]+) vertices, '
                                      '(?P<triangles>[\d,]+) triangles\) '
                                      'in (?P<milliseconds>[\d,]+) ms\.' )

re_scene_bounding_box   = re.compile(
            '\s*scene bounding box\: '
            '\((?P<pt1_x>[0-9.-]+)\, (?P<pt1_y>[0-9.-]+)\, (?P<pt1_z>[0-9.-]+)\)\-'
            '\((?P<pt2_x>[0-9.-]+)\, (?P<pt2_y>[0-9.-]+)\, (?P<pt2_z>[0-9.-]+)\)\.' )

re_scene_diameter       = re.compile( '\s*scene diameter\: (?P<diameter>[0-9.-]+)\.' )

re_while_loading_mesh_object = re.compile( '\s*while loading mesh object \"(?P<object>[\w.]+)\"\:(?P<problem>.+)' )

# option regex
re_opt_frame_settings_trigger          = re.compile( '\s*frame settings\:' )
re_opt_frame_settings_resolution       = re.compile( '\s*resolution\s+(?P<x_resolution>[\d,]+) x (?P<y_resolution>[\d,]+)' )
re_opt_frame_settings_tile_size        = re.compile( '\s*tile size\s+(?P<x_resolution>[\d,]+) x (?P<y_resolution>[\d,]+)' )
re_opt_frame_settings_pixel_format     = re.compile( '\s*pixel format\s+(?P<pixel_format>[\w]+)' )
re_opt_frame_settings_filter           = re.compile( '\s*filter\s+(?P<filter>[\w]+)' )
re_opt_frame_settings_filter_size      = re.compile( '\s*filter size\s+(?P<filter_size>[\d.]+)' )
re_opt_frame_settings_color_space      = re.compile( '\s*color space\s+(?P<color_space>[\w]+)' )
re_opt_frame_settings_premult_alpha    = re.compile( '\s*premult\. alpha\s+(?P<premult_alpha>(on|off))' )
re_opt_frame_settings_clamping         = re.compile( '\s*clamping\s+(?P<clamping>(on|off))' )
re_opt_frame_settings_gamma_correction = re.compile( '\s*gamma correction\s+(?P<gamma_correction>[\w]+)' )
re_opt_frame_settings_crop_window      = re.compile( '\s*crop window\s+\((?P<x_top_left>[\d,]+), (?P<y_top_left>[\d,]+)\)-\((?P<x_bottom_right>[\d,]+), (?P<y_bottom_right>[\d,]+)\)' )

datetime_str_format = '%Y-%m-%dT%H:%M:%S.%fZ'

class ASLogLine( object ) :

    __slots__ = ( 'line'           ,  # the whole line string
                  'number'         ,  # the line number
                  '_timestamp'     ,
                  '_raw_timestamp' ,
                  'thread_id'      ,
                  'vm'             ,
                  'msg_cat'        ,
                  'msg_rest'       ,  # Everything after the pipe
                  'msg_content'    )  # Details of msg_rest

    def __init__( self, line, number ) :

        assert isinstance( line  , basestring )
        assert isinstance( number, int        )

        self.line           = line
        self.number         = number
        self._timestamp     = None
        self._raw_timestamp = None
        self.thread_id      = None
        self.vm             = None
        self.msg_cat        = None
        self.msg_rest       = None
        self.msg_content    = dict()

        self._parse()

    def _parse( self ) :

        match_grp = re_main.match( self.line )
        if not match_grp :
            print "Warning, can't parse line %d : %s" % ( i , line )
            return
        self._raw_timestamp = match_grp.group( 'timestamp'  )
        self.thread_id      = int( match_grp.group( 'thread_id'  ) )
        self.vm             = int( match_grp.group( 'vm'         ) )
        self.msg_cat        = match_grp.group( 'msg_cat'    )
        self.msg_rest       = match_grp.group( 'msg_rest'   )



    @property
    def is_empty( self ) :
        return len( self.line ) == 0

    @property
    def timestamp( self ) :
        if self._timestamp is None and self._raw_timestamp is not None :
            self._timestamp = datetime.datetime.strptime( self._raw_timestamp ,
                                                          datetime_str_format )
        return self._timestamp


class ASLogParser( object ) :

    def __init__( self, log_file ) :

        self._log_file   = log_file
        self._lines_data = list()

        # options
        self._options    = dict()

        # ranges
        self._ranges                     = dict()
        self._ranges[ 'first_datetime' ] = None
        self._ranges[ 'last_datetime'  ] = None
        self._ranges[ 'vm_max'         ] = 0
        self._ranges[ 'vm_max_str_len' ] = 0 # for nice output

        self._parse()

    @property
    def _lines( self ) :
        """Read every lines from the log file."""
        with open( self._log_file, 'r' ) as log_file :
            yield log_file.readline()

    def _parse( self ) :

        triggereds = set()

        for i, line in enumerate( self._lines ) :

            line_data = ASLogLine(line, i)

            vm_str_len = len( str( line_data.vm ) )
            if vm_str_len > self._ranges[ 'vm_max_str_len' ] :
                self._ranges[ 'vm_max_str_len' ] = vm_str_len

            msg_rest    = line_data.msg_rest
            msg_content = line_data.msg_content
            msg_content[ 'type' ] = None

            # Loading project file
            match_grp = re_project_file_path.match( msg_rest )
            if match_grp :
                msg_content[ 'type'              ] = 'loading_project_file'
                msg_content[ 'project_file_path' ] = match_grp.group( 'project_file_path' )

            # Opening texture file
            match_grp = re_opening_texture_file.match( msg_rest )
            if match_grp :
                msg_content[ 'type'         ] = 'opening_texture_file'
                msg_content[ 'texture_path' ] = match_grp.group( 'texture_path' )

            # Rendering progress
            match_grp = re_rendering_progress.match( msg_rest )
            if match_grp :
                msg_content[ 'type'       ] = 'rendering_progress'
                msg_content[ 'percentage' ] = float( match_grp.group( 'percentage' ) )

            # Write final image
            match_grp = re_wrote_image_file.match( msg_rest )
            if match_grp :
                msg_content[ 'type'         ] = 'wrote_image_file'
                msg_content[ 'image_path'   ] = match_grp.group( 'image_path' )

                raw_milliseconds = match_grp.group( 'milliseconds' )
                raw_milliseconds = raw_milliseconds.replace( ',', '' )
                msg_content[ 'milliseconds' ] = int( raw_milliseconds )

            # Mesh file loaded
            match_grp = re_loaded_mesh_file.match( msg_rest )
            if match_grp :
                msg_content[ 'type'         ] = 'loaded_mesh_file'
                msg_content[ 'mesh_path'    ] = match_grp.group( 'mesh_path' )

                raw_objects = match_grp.group( 'objects' )
                raw_objects = raw_objects.replace( ',', '' )
                msg_content[ 'objects'      ] = int( raw_objects )

                raw_vertices = match_grp.group( 'vertices' )
                raw_vertices = raw_vertices.replace( ',', '' )
                msg_content[ 'vertices'     ] = int( raw_vertices )

                raw_triangles = match_grp.group( 'triangles' )
                raw_triangles = raw_triangles.replace( ',', '' )
                msg_content[ 'triangles'    ] = int( raw_triangles )

                raw_milliseconds = match_grp.group( 'milliseconds' )
                raw_milliseconds = raw_milliseconds.replace( ',', '' )
                msg_content[ 'milliseconds' ] = int( raw_milliseconds )

            # Scene bounding box
            match_grp = re_scene_bounding_box.match( msg_rest )
            if match_grp :
                msg_content[ 'type'         ] = 'scene_bounding_box'
                msg_content[ 'bounding_box' ] = ( ( float( match_grp.group( 'pt1_x' ) ) ,
                                                    float( match_grp.group( 'pt1_y' ) ) ,
                                                    float( match_grp.group( 'pt1_z' ) ) ) ,
                                                  ( float( match_grp.group( 'pt2_x' ) ) ,
                                                    float( match_grp.group( 'pt2_y' ) ) ,
                                                    float( match_grp.group( 'pt2_z' ) ) ) )

            # Scene diameter
            match_grp = re_scene_diameter.match( msg_rest )
            if match_grp :
                msg_content[ 'type'     ] = 'scene_diameter'
                msg_content[ 'diameter' ] = match_grp.group( 'diameter' )

            match_grp = re_while_loading_mesh_object.match( msg_rest )
            if match_grp :
                msg_content[ 'type'    ] = 'while_loading_mesh_object'
                msg_content[ 'object'  ] = match_grp.group( 'object'  )
                msg_content[ 'problem' ] = match_grp.group( 'problem' )

            self._lines_data.append( line_data )

            ################################################################
            # Options
            ################################################################
            if 'frame_settings' in triggereds :
                ############################################################
                # Frame settings
                ############################################################
                if not 'frame_settings' in self._options :
                    self._options[ 'frame_settings' ] = dict()

                match_grp = re_opt_frame_settings_resolution.match( msg_rest )
                if match_grp :
                    x = match_grp.group( 'x_resolution' )
                    x = int( x.replace( ',', '' ) )
                    y = match_grp.group( 'y_resolution' )
                    y = int( y.replace( ',', '' ) )
                    self._options[ 'frame_settings' ][ 'resolution' ] = ( x, y )

                match_grp = re_opt_frame_settings_tile_size.match( msg_rest )
                if match_grp :
                    x = match_grp.group( 'x_resolution' )
                    x = int( x.replace( ',', '' ) )
                    y = match_grp.group( 'y_resolution' )
                    y = int( y.replace( ',', '' ) )
                    self._options[ 'frame_settings' ][ 'tile_size' ] = ( x, y )

                match_grp = re_opt_frame_settings_pixel_format.match( msg_rest )
                if match_grp :
                    self._options[ 'frame_settings' ][ 'pixel_format' ] = \
                                               match_grp.group( 'pixel_format' )

                match_grp = re_opt_frame_settings_filter.match( msg_rest )
                if match_grp :
                    self._options[ 'frame_settings' ][ 'filter' ] = \
                                                     match_grp.group( 'filter' )

                match_grp = re_opt_frame_settings_filter_size.match( msg_rest )
                if match_grp :
                    self._options[ 'frame_settings' ][ 'filter_size' ] = \
                                       float( match_grp.group( 'filter_size' ) )

                match_grp = re_opt_frame_settings_color_space.match( msg_rest )
                if match_grp :
                    self._options[ 'frame_settings' ][ 'color_space' ] = \
                                                match_grp.group( 'color_space' )

                match_grp = re_opt_frame_settings_premult_alpha.match( msg_rest )
                if match_grp :
                    premult_alpha = match_grp.group( 'premult_alpha' )
                    self._options[ 'frame_settings' ][ 'premult_alpha' ] = \
                                    True if premult_alpha == 'on' else False

                match_grp = re_opt_frame_settings_clamping.match( msg_rest )
                if match_grp :
                    clamping = match_grp.group( 'clamping' )
                    self._options[ 'frame_settings' ][ 'clamping' ] = \
                                             True if clamping == 'on' else False

                match_grp = re_opt_frame_settings_gamma_correction.match( msg_rest )
                if match_grp :
                    self._options[ 'frame_settings' ][ 'gamma_correction' ] = \
                                  float( match_grp.group( 'gamma_correction' ) )

                match_grp = re_opt_frame_settings_crop_window.match( msg_rest )
                if match_grp :
                    x_top_left = match_grp.group( 'x_top_left' )
                    x_top_left = int( x_top_left.replace( ',', '' ) )
                    y_top_left = match_grp.group( 'y_top_left' )
                    y_top_left = int( y_top_left.replace( ',', '' ) )
                    x_bottom_right = match_grp.group( 'x_bottom_right' )
                    x_bottom_right = int( x_bottom_right.replace( ',', '' ) )
                    y_bottom_right = match_grp.group( 'y_bottom_right' )
                    y_bottom_right = int( y_bottom_right.replace( ',', '' ) )
                    self._options[ 'frame_settings' ][ 'crop_window' ] = \
                                                          ( x_top_left     ,
                                                            y_top_left     ,
                                                            x_bottom_right ,
                                                            y_bottom_right )
                    triggereds.remove( 'frame_settings' )

            match_grp = re_opt_frame_settings_trigger.match( msg_rest )
            if match_grp :
                triggereds.add( 'frame_settings' )

            ################################################################
            # Update graph ranges if needed
            ################################################################
            if self._ranges[ 'first_datetime' ] is None :
                self._ranges[ 'first_datetime' ] = timestamp

            if self._ranges[ 'last_datetime' ] is None \
               or self._ranges[ 'last_datetime' ] < timestamp :
                self._ranges[ 'last_datetime' ] = timestamp

            if vm > self._ranges[ 'vm_max' ] :
                self._ranges[ 'vm_max' ] = vm

    def export_to_csv( self, path ) :
        """Export the current parsed log to csv at the given path."""

        csv_file   = open( path, 'wb' )
        csv_writer = csv.writer( csv_file                  ,
                                 delimiter = ';'           ,
                                 quotechar = '"'           ,
                                 quoting   = csv.QUOTE_ALL )
        csv_writer.writerow( [ 'Time in sec' ,
                               'VM'          ,
                               'Progress'    ] )

        progress = 0.0

        for line_data in self._lines_data :

            line_timestamp  = line_data.timestamp
            line_delta_time = line_timestamp - self._first_datetime
            time_in_sec     = line_delta_time.total_seconds()

            # Second part of the message
            msg_content = line_data.msg_content

            if msg_content[ 'type' ] == 'rendering_progress' :
                progress = msg_content[ 'percentage' ]

            csv_writer.writerow( [ float( time_in_sec  ) ,
                                   float( line_data.vm ) ,
                                   float( progress     ) ] )

        csv_file.close()

        print "Exported to csv file : %s" % path

    def export_to_gnuplot( self, file_path ) :
        """Export the .csv and the .plot file to execute with gnuplot.

        The given file path shouldn't have file extention.
        """

        csv_path  = file_path + ".csv"
        plot_path = file_path + ".plot"

        self.export_to_csv( csv_path )

        # Add few margin for cosmetic graph
        vm_for_graph = self._vm_max# + self._vm_max*0.05+1
        last_second = ( self._last_datetime - self._first_datetime ).total_seconds()
        margin_second = last_second*0.05+1
        last_second += margin_second

        # Generate the gnuplot string
        raw_str  = str()
        #raw_str += 'set terminal x11 persist\n'
        raw_str += 'set xtics font "Times-Roman, 6"\n'
        raw_str += 'set datafile separator ";"\n'
        raw_str += 'plot "%s" using 1:2 with lines title columnhead axes x1y1,' % csv_path
        raw_str += ' "" using 1:3 with lines title columnhead axes x1y2\n'
        raw_str += 'set yrange [0:%s]\n' % int( vm_for_graph )
        raw_str += 'set ylabel "VM: 0-%s MB"\n' % int( vm_for_graph)
        #raw_str += 'set autoscale y\n'
        raw_str += 'set autoscale y2\n'
        #raw_str += 'set y2range [0:100]\n' # progress is percentage
        #raw_str += 'set y2label "Progress"\n'
        #raw_str += 'set ytics 60\n'
        #raw_str += 'set mxtics 1\n'
        raw_str += 'set xdata time\n'
        raw_str += 'set timefmt "%s"\n'
        raw_str += 'set format x "%Hh%Mm"\n'
        raw_str += 'set xlabel "Time"\n'
        #raw_str += 'set ylabel "Angle"\n'
        #raw_str += 'set xrange [%s:%s]\n' % ( -margin_second, last_second )
        raw_str += 'set title "%s"\n' % os.path.basename( self._log_file )
        raw_str += 'replot\n'
        raw_str += 'pause -1  "Hit return to continue"\n'

        with open( plot_path, 'w' ) as plot_file :
            plot_file.write( raw_str )

        print "Exported to gnuplot script : %s" % plot_path

    @property
    def loaded_mesh_files( self ) :
        """Return a list of loaded mesh files found in the log."""
        return self._path_get( 'mesh_path', 'loaded_mesh_file' )

    @property
    def scene_bounding_box( self ) :
        """Return the scene bounding box ((x,y,z),(x,y,z))."""
        return self._path_get( 'bounding_box', 'scene_bounding_box' )

    @property
    def scene_diameter( self ) :
        """Return the scene diameter."""
        return self._path_get( 'diameter', 'scene_diameter' )

    @property
    def loaded_project_files( self ) :
        """Return a list of loaded project files found in the log."""
        return self._path_get( 'project_file_path', 'loading_project_file' )

    @property
    def opened_texture_files( self ) :
        """Return a list of opened texture files found in the log."""
        return self._path_get( 'texture_path', 'opening_texture_file' )

    def _path_get( self, msg_cat, type ) :
        """Return the value of the specified key for the specified message type."""
        return [ x.msg_content[ msg_cat ] for x in self._lines_data
                if x.msg_content[ 'type' ] == type ]

    @property
    def lines_data( self ) :
        return self._lines_data


    '''def lines( self                          ,
               levels      = [ 'info'      ,
                               'warning'   ,
                               'error'     ,
                               'fatal'     ] ,
               prefix      = [ 'timestamp' ,
                               'thread_id' ,
                               'vm'        ,
                               'msg_cat'   ] ,
               text_filter = None            ) :
        """Return the lines of the log depending on the given filters."""
        lines = list()
        for line_data in self._lines_data :

            if line_data[ 'msg_cat' ] not in levels :
                continue

            line = str()
            if 'timestamp' in prefix :
                line  = '%s '    % line_data[ 'timestamp' ].strftime( datetime_str_format )
            if 'thread_id' in prefix :
                line += '<%s> '  % str( line_data[ 'thread_id' ] ).zfill( 3 )
            if 'vm' in prefix :
                vm_str = str( line_data[ 'vm' ] )
                line += ' ' * ( self._vm_max_str_len - len( vm_str ) )
                line += '%s MB ' % vm_str
            if 'msg_cat' in prefix :
                line += '%s'     % line_data[ 'msg_cat' ]
                missing_spaces = 8 - len( line_data[ 'msg_cat' ] )
                line += ' ' * missing_spaces
            if prefix :
                line += '|'

            line += line_data[ 'msg_rest' ]

            lines.append( line )

        return lines'''

    @property
    def render_options( self ) :
        """Return the render options (dict) found in the log."""
        return self._options

    @property
    def ranges( self ) :
        """Return the differents min/max values (dict) found in the log."""
        return self._ranges


class ASLogParserUI( QtGui.QMainWindow ) :
    def __init__( self ) :
        QtGui.QMainWindow.__init__( self )

        # recent_log_files_listWidget.as_log_file_path
        # filtered_log_listWidget.as_line_data

        # Icons
        self._show_icons      = True
        script_dir            = os.path.dirname( __file__ )
        icon_dir              = os.path.join( script_dir, 'icons' )
        texture_icon_path     = os.path.join( icon_dir, 'appleseed-texture-black-icon.svg'    )
        mesh_icon_path        = os.path.join( icon_dir, 'appleseed-mesh-black-icon.svg'       )
        bbox_icon_path        = os.path.join( icon_dir, 'appleseed-scene-bounding-box-icon'   )
        diameter_icon_path    = os.path.join( icon_dir, 'appleseed-scene-diameter-icon'       )
        progress_icon_path    = os.path.join( icon_dir, 'appleseed-progress-black-icon.svg'   )
        resolution_icon_path  = os.path.join( icon_dir, 'appleseed-resolution-black-icon.svg' )
        tile_icon_path        = os.path.join( icon_dir, 'appleseed-tile-black-icon.svg'       )
        empty_icon_path       = os.path.join( icon_dir, 'appleseed-empty-icon.svg'            )
        self.icons            = dict()
        self.icons[ 'progress'   ] = QtGui.QIcon( progress_icon_path   )
        self.icons[ 'texture'    ] = QtGui.QIcon( texture_icon_path    )
        self.icons[ 'mesh'       ] = QtGui.QIcon( mesh_icon_path       )
        self.icons[ 'bbox'       ] = QtGui.QIcon( bbox_icon_path       )
        self.icons[ 'diameter'   ] = QtGui.QIcon( diameter_icon_path   )
        self.icons[ 'resolution' ] = QtGui.QIcon( resolution_icon_path )
        self.icons[ 'tile'       ] = QtGui.QIcon( tile_icon_path       )
        self.icons[ 'empty'      ] = QtGui.QIcon( empty_icon_path      )
        self.icons[ 'open'       ] = QtGui.QIcon.fromTheme( 'document-open' )
        self.icons[ 'copy'       ] = QtGui.QIcon.fromTheme( 'edit-copy'     )
        self.icons[ 'remove'     ] = QtGui.QIcon.fromTheme( 'list-remove'   )

        self._old_dir          = QtCore.QDir.homePath() # for file dialog "open log file"
        self._current_log      = None
        #self._current_log_id   = None
        self._log_datas        = dict() # a dict of ASLogParser, key is the log file path
        self._recent_log_order = list() # store the order of rencent log files
        self._log_levels       = set( [ 'info', 'warning', 'error', 'fatal' ] )
        self._log_prefixes     = set( [ 'timestamp', 'thread_id', 'vm', 'msg_cat' ] )

        uic.loadUi( 'appleseed_log_parser.ui', self )

        self.recent_log_files_listWidget.clicked.connect( self.cb_recent_log_files_changed )
        self.recent_log_files_listWidget.customContextMenuRequested.connect( self.cb_recent_log_view_menu )

        self.all_cb.clicked.connect(     functools.partial( self.cb_log_level_changed, 'ALL'     ) )
        self.info_cb.clicked.connect(    functools.partial( self.cb_log_level_changed, 'info'    ) )
        self.warning_cb.clicked.connect( functools.partial( self.cb_log_level_changed, 'warning' ) )
        self.error_cb.clicked.connect(   functools.partial( self.cb_log_level_changed, 'error'   ) )
        self.fatal_cb.clicked.connect(   functools.partial( self.cb_log_level_changed, 'fatal'   ) )

        self.timestamp_cb.clicked.connect( functools.partial( self.cb_log_prefix_changed, 'timestamp' ) )
        self.thread_id_cb.clicked.connect( functools.partial( self.cb_log_prefix_changed, 'thread_id' ) )
        self.vm_cb.clicked.connect(        functools.partial( self.cb_log_prefix_changed, 'vm'        ) )
        self.msg_cat_cb.clicked.connect(   functools.partial( self.cb_log_prefix_changed, 'msg_cat'  ) )

        self.filtered_log_listWidget.customContextMenuRequested.connect( self.cb_filtered_log_view_menu )

        #self.frame_setting_resolution_label.setPixmap( self.icons[ 'resolution' ].pixmap( 16, 16 ) )
        #self.frame_setting_tile_size_label.setPixmap( self.icons[ 'tile' ].pixmap( 16, 16 ) )

        self.refresh_log_level_cb()
        self.refresh_log_prefix_cb()
        self.refresh_options_tab()

    def on_icon_cb_clicked( self, checked = None ) :
        if checked is None: return

        self._show_icons = checked

        self.refresh_filtered_log_view()

    def on_action_Open_log_file_triggered( self, checked = None ) :
        if checked is None: return

        file_path = str( QtGui.QFileDialog.getOpenFileName( self            ,
                                                            'Open log file' ,
                                                            self._old_dir   ) )
        if file_path :
            self._old_dir = os.path.dirname( file_path )

            self.change_current_log_file( file_path )

    def on_action_Quit_triggered( self, checked = None ) :
        """Close the app."""
        if checked is None: return
        self.close()

    def cb_recent_log_files_changed( self ) :

        selected_item = self.recent_log_files_listWidget.currentItem()
        self.change_current_log_file( selected_item.as_log_file_path )

    def change_current_log_file( self, file_path ) :
        self._current_log = file_path

        # if we don't have the log stored yet, we parse it.
        if not file_path in self._log_datas :
            self._log_datas[ file_path ] = ASLogParser( file_path )
            self._recent_log_order.append( file_path )

        self.refresh_filtered_log_view()
        self.refresh_options_tab()
        self.refresh_recent_log_files_listWidget()

    def remove_log_entry( self, log_file_path ) :
        """Remove the given recent log entry."""

        del self._log_datas[ log_file_path ]
        self._recent_log_order.remove( log_file_path )

        self.refresh_recent_log_files_listWidget()

    def refresh_recent_log_files_listWidget( self ) :
        """Refresh the recent listWidget."""

        self.recent_log_files_listWidget.clear()

        for log_file_path in self._recent_log_order :
            # TODO: put the label creation in a separate function
            log_file_name = os.path.basename( log_file_path )
            log_dir_name  = os.path.dirname( log_file_path )
            label         = '%s (in %s)' % ( log_file_name, log_dir_name )
            current_item  = QtGui.QListWidgetItem( label )
            current_item.as_log_file_path = log_file_path
            self.recent_log_files_listWidget.addItem( current_item )
            if log_file_path == self._current_log :
                self.recent_log_files_listWidget.setCurrentItem( current_item )

    def refresh_filtered_log_view( self ) :

        self.filtered_log_listWidget.clear()

        current_log_data = self._log_datas[ self._current_log ]

        # get ranges we need
        vm_max_str_len = current_log_data.ranges[ 'vm_max_str_len' ]

        #levels   = list( self._log_levels )
        #prefixes = list( self._log_prefixes )

        #lines = current_log_data.lines( levels , prefixes )#,
        '''text_filter = None            )'''
        for line_data in current_log_data.lines_data :

            # Skip unwanted levels
            if line_data[ 'msg_cat' ] not in self._log_levels :
                continue

            ####################################################################
            # Generate the log line
            ####################################################################
            line = str()
            if 'timestamp' in self._log_prefixes :
                line  = '%s '    % line_data[ 'timestamp' ].strftime( datetime_str_format )

            if 'thread_id' in self._log_prefixes :
                line += '<%s> '  % str( line_data[ 'thread_id' ] ).zfill( 3 )

            if 'vm' in self._log_prefixes :
                vm_str = str( line_data[ 'vm' ] )
                line += ' ' * ( vm_max_str_len - len( vm_str ) )
                line += '%s MB ' % vm_str

            if 'msg_cat' in self._log_prefixes :
                line += '%s'     % line_data[ 'msg_cat' ]
                missing_spaces = 8 - len( line_data[ 'msg_cat' ] )
                line += ' ' * missing_spaces

            if self._log_prefixes :
                line += '|'
                line += line_data[ 'msg_rest' ]
            else :
                # remove the first char (a space)
                line += line_data[ 'msg_rest' ][1:]

            current_item = QtGui.QListWidgetItem( line )
            current_item.as_line_data = line_data

            ####################################################################
            # Find the accurate icon
            ####################################################################
            if self._show_icons :
                icon = None
                line_type = line_data[ 'msg_content' ][ 'type' ]
                if line_type in [ 'loaded_mesh_file', 'while_loading_mesh_object' ] :
                    icon = self.icons[ 'mesh'     ]
                elif line_type == 'scene_bounding_box'   :
                    icon = self.icons[ 'bbox'     ]
                elif line_type == 'scene_diameter'   :
                    icon = self.icons[ 'diameter' ]
                elif line_type == 'rendering_progress'   :
                    icon = self.icons[ 'progress' ]
                elif line_type == 'opening_texture_file' :
                    icon = self.icons[ 'texture'  ]
                else :
                    icon = self.icons[ 'empty'    ]

                if icon :
                    current_item.setIcon( icon )



            self.filtered_log_listWidget.addItem( current_item )

        self.filtered_log_listWidget.setIconSize( QtCore.QSize(13, 13) )

    def refresh_options_tab( self ) :

        options = dict()
        if self._current_log in self._log_datas :
            current_log_data = self._log_datas[ self._current_log ]
            options          = current_log_data.render_options

        ########################################################################
        # Frame Setting
        ########################################################################
        frame_setting_options = dict()
        if 'frame_settings' in options :
            frame_setting_options = options[ 'frame_settings' ]

        label_str = 'Not found'
        if 'resolution' in frame_setting_options :
            x, y = frame_setting_options[ 'resolution' ]
            label_str = '%s x %s' % ( y, x )
        self.frame_setting_resolution_value_label.setText( label_str )

        label_str = 'Not found'
        if 'tile_size' in frame_setting_options :
            x, y = frame_setting_options[ 'tile_size' ]
            label_str = '%s x %s' % ( y, x )
        self.frame_setting_tile_size_value_label.setText( label_str )

        label_str = 'Not found'
        if 'pixel_format' in frame_setting_options :
            label_str = str( frame_setting_options[ 'pixel_format' ] )
        self.frame_setting_pixel_format_value_label.setText( label_str )

        label_str = 'Not found'
        if 'filter' in frame_setting_options :
            label_str = str( frame_setting_options[ 'filter' ] )
        self.frame_setting_filter_value_label.setText( label_str )

        label_str = 'Not found'
        if 'filter_size' in frame_setting_options :
            label_str = str( frame_setting_options[ 'filter_size' ] )
        self.frame_setting_filter_size_value_label.setText( label_str )

        label_str = 'Not found'
        if 'color_space' in frame_setting_options :
            label_str = str( frame_setting_options[ 'color_space' ] )
        self.frame_setting_color_space_value_label.setText( label_str )

        label_str = 'Not found'
        if 'premult_alpha' in frame_setting_options :
            label_str = 'on' if frame_setting_options[ 'premult_alpha' ] else 'off'
        self.frame_setting_premult_alpha_value_label.setText( label_str )

        label_str = 'Not found'
        if 'clamping' in frame_setting_options :
            label_str = 'on' if frame_setting_options[ 'clamping' ] else 'off'
        self.frame_setting_clamping_value_label.setText( label_str )

        label_str = 'Not found'
        if 'gamma_correction' in frame_setting_options :
            label_str = str( frame_setting_options[ 'gamma_correction' ] )
        self.frame_setting_gamma_correction_value_label.setText( label_str )

        label_str = 'Not found'
        if 'crop_window' in frame_setting_options :
            label_str = '%s x %s - %s x %s' % frame_setting_options[ 'crop_window' ]
        self.frame_setting_crop_window_value_label.setText( label_str )


    def refresh_log_level_cb( self ) :

        state = QtCore.Qt.Checked if 'info' in self._log_levels else QtCore.Qt.Unchecked
        if state != self.info_cb.checkState() :
            self.info_cb.setCheckState( state )

        state = QtCore.Qt.Checked if 'warning' in self._log_levels else QtCore.Qt.Unchecked
        if state != self.warning_cb.checkState() :
            self.warning_cb.setCheckState( state )

        state = QtCore.Qt.Checked if 'error' in self._log_levels else QtCore.Qt.Unchecked
        if state != self.error_cb.checkState() :
            self.error_cb.setCheckState( state )

        state = QtCore.Qt.Checked if 'fatal' in self._log_levels else QtCore.Qt.Unchecked
        if state != self.fatal_cb.checkState() :
            self.fatal_cb.setCheckState( state )

        all_cb_state = self.all_cb.checkState()
        if self._log_levels == set( [ 'info', 'warning', 'error', 'fatal' ] ) :
            if all_cb_state != QtCore.Qt.Checked :
                self.all_cb.setCheckState( QtCore.Qt.Checked )
        elif len( self._log_levels ) :
            if all_cb_state != QtCore.Qt.PartiallyChecked :
                self.all_cb.setCheckState( QtCore.Qt.PartiallyChecked )
        elif all_cb_state != QtCore.Qt.Unchecked :
            self.all_cb.setCheckState( QtCore.Qt.Unchecked )

    def refresh_log_prefix_cb( self ) :

        state = QtCore.Qt.Checked if self._show_icons else QtCore.Qt.Unchecked
        if state != self.icon_cb.checkState() :
            self.icon_cb.setCheckState( state )

        state = QtCore.Qt.Checked if 'timestamp' in self._log_prefixes else QtCore.Qt.Unchecked
        if state != self.timestamp_cb.checkState() :
            self.timestamp_cb.setCheckState( state )

        state = QtCore.Qt.Checked if 'thread_id' in self._log_prefixes else QtCore.Qt.Unchecked
        if state != self.thread_id_cb.checkState() :
            self.thread_id_cb.setCheckState( state )

        state = QtCore.Qt.Checked if 'vm' in self._log_prefixes else QtCore.Qt.Unchecked
        if state != self.vm_cb.checkState() :
            self.vm_cb.setCheckState( state )

        state = QtCore.Qt.Checked if 'msg_cat' in self._log_prefixes else QtCore.Qt.Unchecked
        if state != self.msg_cat_cb.checkState() :
            self.msg_cat_cb.setCheckState( state )

    def cb_filtered_log_view_menu( self, pt ) :
        """Generate the menu for the log view."""

        def short_label( text ) :
            """Used to shorten the action label complement."""
            if len( text ) > 30 :
                return '"%s..."' % text[:27]
            else :
                return '"%s"' % text

        # Get selection
        selected_items = self.filtered_log_listWidget.selectedItems()

        menu = QtGui.QMenu( self )

        if len( selected_items ) == 1 :

            selected_item = selected_items[0]
            line_data = selected_item.as_line_data

            # Copy visible line
            act = QtGui.QAction( self.icons[ 'copy' ], 'Copy' , menu )
            act.triggered.connect( functools.partial( self.cb_copy, selected_item.text() ) )
            menu.addAction( act )

            # Copy whole line (only if user has changed the line view )
            if self._log_levels != set( [ 'info', 'warning', 'error', 'fatal' ] ) or \
               self._log_prefixes != set( [ 'timestamp', 'thread_id', 'vm', 'msg_cat' ] ) :
                act = QtGui.QAction( self.icons[ 'copy' ], 'Copy whole line' , menu )
                act.triggered.connect( functools.partial( self.cb_copy, line_data[ 'line' ] ) )
                menu.addAction( act )

            if line_data[ 'msg_content' ][ 'type' ] == 'loading_project_file' :
                project_file_path = line_data[ 'msg_content' ][ 'project_file_path' ]
                label = 'Copy project file path: %s' % short_label( project_file_path )
                act = QtGui.QAction( self.icons[ 'copy' ] ,
                                     label                ,
                                     menu                 )
                act.triggered.connect( functools.partial( self.cb_copy      ,
                                                          project_file_path ) )
                menu.addAction( act )

            elif line_data[ 'msg_content' ][ 'type' ] == 'opening_texture_file' :
                texture_path = line_data[ 'msg_content' ][ 'texture_path' ]
                label = 'Copy texture file path: %s' % short_label( texture_path )
                act = QtGui.QAction( self.icons[ 'copy' ] ,
                                     label                ,
                                     menu                 )
                act.triggered.connect( functools.partial( self.cb_copy ,
                                                          texture_path ) )
                menu.addAction( act )

            elif line_data[ 'msg_content' ][ 'type' ] == 'wrote_image_file' :
                image_path = line_data[ 'msg_content' ][ 'image_path' ]
                label = 'Copy image file path: %s' % short_label( image_path )
                act = QtGui.QAction( self.icons[ 'copy' ] ,
                                     label                ,
                                     menu                 )
                act.triggered.connect( functools.partial( self.cb_copy ,
                                                          image_path   ) )
                menu.addAction( act )

            elif line_data[ 'msg_content' ][ 'type' ] == 'loaded_mesh_file' :
                mesh_path = line_data[ 'msg_content' ][ 'mesh_path' ]
                label = 'Copy image file path: %s' % short_label( mesh_path )
                act = QtGui.QAction( self.icons[ 'copy' ] ,
                                     label                ,
                                     menu                 )
                act.triggered.connect( functools.partial( self.cb_copy ,
                                                          mesh_path    ) )
                menu.addAction( act )


        elif len( selected_items ) > 1 :

            raw_text = str() # The text that will be put in the clipboard

            for selected_item in selected_items :
                raw_text += '%s\n' % selected_item.text()

            act = QtGui.QAction( self.icons[ 'copy' ], 'Copy' , menu )
            act.triggered.connect( functools.partial( self.cb_copy, raw_text ) )
            menu.addAction( act )

            # Copy whole lines
            if self._log_levels != set( [ 'info', 'warning', 'error', 'fatal' ] ) or \
               self._log_prefixes != set( [ 'timestamp', 'thread_id', 'vm', 'msg_cat' ] ) :

                raw_str = str()
                for selected_item in selected_items :
                    line_data = selected_item.as_line_data
                    raw_str += '%s\n' % line_data[ 'line' ]

                act = QtGui.QAction( self.icons[ 'copy' ], 'Copy whole lines' , menu )
                act.triggered.connect( functools.partial( self.cb_copy, raw_str ) )
                menu.addAction( act )

        menu.exec_( self.filtered_log_listWidget.mapToGlobal( pt ) )

    def cb_recent_log_view_menu( self, pt ) :
        """Generate the context menu for the recent log file listWidget."""

        # Get selection
        selected_items = self.recent_log_files_listWidget.selectedItems()

        menu = QtGui.QMenu( self )

        if len( selected_items ) == 1 :

            selected_item = selected_items[0]
            log_file_path = selected_item.as_log_file_path

            # Copy log file path
            act = QtGui.QAction( self.icons[ 'copy' ], 'Copy file path' , menu )
            act.triggered.connect( functools.partial( self.cb_copy, log_file_path ) )
            menu.addAction( act )

            # Copy log folder path
            log_dir_path = os.path.dirname( log_file_path )
            act = QtGui.QAction( self.icons[ 'copy' ], 'Copy folder path' , menu )
            act.triggered.connect( functools.partial( self.cb_copy, log_dir_path ) )
            menu.addAction( act )

            # Open in folder path
            log_dir_path = os.path.dirname( log_file_path )
            act = QtGui.QAction( self.icons[ 'open' ], 'Open log folder' , menu )
            act.triggered.connect( functools.partial( self.cb_dir_open, log_dir_path ) )
            menu.addAction( act )

            # Remove entry
            act = QtGui.QAction( self.icons[ 'remove' ], 'Remove log entry' , menu )
            act.triggered.connect( functools.partial( self.remove_log_entry, log_file_path ) )
            menu.addAction( act )

        menu.exec_( self.recent_log_files_listWidget.mapToGlobal( pt ) )

    def cb_log_level_changed( self, *args ) :
        log_type = args[0]
        state    = args[1]

        if log_type == "ALL" :
            if state :
                self._log_levels = set( [ 'info', 'warning', 'error', 'fatal' ] )
            else :
                self._log_levels = set()
        else :
            if state :
                self._log_levels.add( log_type )
            else :
                self._log_levels.remove( log_type )

        self.refresh_log_level_cb()
        self.refresh_filtered_log_view()

    def cb_log_prefix_changed( self, *args ) :
        prefix = args[0]
        state  = args[1]

        if state :
            self._log_prefixes.add( prefix )
        else :
            self._log_prefixes.remove( prefix )

        self.refresh_filtered_log_view()

    def cb_copy( self, *args ) :
        """Copy the given text to clipboard."""
        clipboard = QtGui.QApplication.clipboard()
        clipboard.setText( args[0] )

    def cb_dir_open( self, *args ) :
        """Open the given folder."""
        print args

def main() :

    #log_parser = ASLogParser( "/home/narann/Bureau/appleseed2.log" )
    #log_parser.export_to_gnuplot( '/home/narann/Bureau/appleseed2' )
    #print log_parser.loaded_mesh_files
    #print log_parser.frame_settings()

    # TODO: add recent files, drag n drop, left right click copy, btd sur filter pour les dernier filter plus preset.
    # TODO: Add mesh icon. double click to copy
    # TODO: Store selected lines
    # TODO: Filter text
    # TODO: Options
    # TODO: Graph
    # TODO: Stats
    # TODO: Show empty line count
    app = QtGui.QApplication( sys.argv )
    script_dir = os.path.dirname( __file__ )
    icon_path = os.path.join( script_dir, 'appleseed-seeds-black-32.png' )
    app.setWindowIcon( QtGui.QIcon( icon_path ) )
    window = ASLogParserUI()
    window.show()
    sys.exit( app.exec_() )

if __name__ == '__main__' :
    main()
